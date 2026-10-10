"""
ACME renewal (Let's Encrypt staging by default, or a local Pebble) with HTTP-01 and Vault KV for secret storage.

What this does and does NOT do
  * plan():    read-only readiness checks (email, CA directory reachable, challenge solver, Vault) + a step list.
  * execute(): new ACME account -> order -> HTTP-01 challenge -> finalize with a fresh private key -> store key +
               full chain in Vault. The private key never touches the database, logs or API responses.
  * It does NOT deploy the new certificate to the endpoint; that step is outside this platform.
  * Verified against a local Pebble; Let's Encrypt staging/production use the same code path but were not exercised.
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

import josepy as jose
import requests
from acme import challenges, client, crypto_util, messages
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app import models
from app.config import settings
from app.renewal.base import ExecResult, PlanSpec, RenewalExecutor, SecretStoreError
from app.renewal.secrets import VaultKvStore
from app.timeutil import ensure_utc

PRODUCTION_DIRECTORY = "https://acme-v02.api.letsencrypt.org/directory"
HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$|^[a-z0-9-]+\.(test|local|internal|example)$",
                      re.I)
MAX_DOMAINS = 10


class ChallengeSolver(Protocol):
    def present(self, domain: str, token: str, key_authorization: str) -> None: ...
    def cleanup(self, domain: str, token: str) -> None: ...
    def check(self) -> Optional[str]:
        """Return a problem description, or None if the solver looks usable."""


class WebrootSolver:
    """Writes the token under <webroot>/.well-known/acme-challenge/ (the web server must serve that on port 80)."""

    def __init__(self, webroot: Optional[str] = None):
        self.webroot = webroot or settings.ACME_WEBROOT_PATH

    def _file(self, token: str) -> Path:
        return Path(self.webroot) / ".well-known" / "acme-challenge" / token

    def present(self, domain: str, token: str, key_authorization: str) -> None:
        f = self._file(token)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(key_authorization)

    def cleanup(self, domain: str, token: str) -> None:
        self._file(token).unlink(missing_ok=True)

    def check(self) -> Optional[str]:
        if not self.webroot:
            return "ACME_WEBROOT_PATH is not set"
        return None if Path(self.webroot).is_dir() else f"webroot {self.webroot} does not exist"


class ChallTestSrvSolver:
    """Pebble's challenge mock (pebble-challtestsrv): registers the HTTP-01 response over its management API."""

    def __init__(self, url: Optional[str] = None):
        self.url = (url or settings.ACME_CHALLTESTSRV_URL or "").rstrip("/")

    def present(self, domain: str, token: str, key_authorization: str) -> None:
        requests.post(f"{self.url}/add-http01", json={"token": token, "content": key_authorization},
                      timeout=10).raise_for_status()

    def cleanup(self, domain: str, token: str) -> None:
        try:
            requests.post(f"{self.url}/del-http01", json={"token": token}, timeout=10)
        except requests.RequestException:
            pass

    def check(self) -> Optional[str]:
        return None if self.url else "ACME_CHALLTESTSRV_URL is not set"


def default_solver() -> ChallengeSolver:
    return ChallTestSrvSolver() if settings.ACME_CHALLENGE == "challtestsrv" else WebrootSolver()


def renewal_domains(cert: models.Certificate) -> tuple[List[str], List[str]]:
    """(domains HTTP-01 can validate, names it cannot: wildcards / IPs / malformed)."""
    names = json.loads(cert.san_list) if cert.san_list else []
    if cert.expected_hostname and cert.expected_hostname not in names:
        names.insert(0, cert.expected_hostname)
    if not names:
        names = [cert.common_name]
    ok, bad = [], []
    for n in dict.fromkeys(names):
        (ok if HOSTNAME.match(n) else bad).append(n)
    return ok, bad


class AcmeExecutor(RenewalExecutor):
    name = "letsencrypt"

    def __init__(self, store: Any = None, solver: Optional[ChallengeSolver] = None,
                 directory_url: Optional[str] = None, email: Optional[str] = None):
        self.store = store if store is not None else VaultKvStore()
        self.solver = solver
        self.directory_url = directory_url or settings.ACME_DIRECTORY_URL
        self.email = email or settings.LETSENCRYPT_EMAIL

    def _solver(self) -> ChallengeSolver:
        return self.solver or default_solver()

    # ---- dry run ---------------------------------------------------------------------------------
    def plan(self, cert: models.Certificate) -> PlanSpec:
        domains, unsupported = renewal_domains(cert)
        problems: List[str] = []
        warnings: List[str] = []
        if not domains:
            problems.append("no DNS name usable for HTTP-01 validation")
        if unsupported:
            warnings.append(f"Not renewable with HTTP-01 (wildcard/IP/invalid): {', '.join(unsupported)}")
            if any(u.startswith("*.") for u in unsupported):
                problems.append("wildcard names need DNS-01 validation, which is not supported")
        if len(domains) > MAX_DOMAINS:
            problems.append(f"more than {MAX_DOMAINS} names on one certificate")
        if not self.email:
            problems.append("LETSENCRYPT_EMAIL is not configured")
        if self.directory_url.rstrip("/") == PRODUCTION_DIRECTORY and not settings.ACME_ALLOW_PRODUCTION:
            problems.append("the production Let's Encrypt directory is blocked (set ACME_ALLOW_PRODUCTION=true to allow)")
        solver_problem = self._solver().check()
        if solver_problem:
            problems.append(f"challenge solver: {solver_problem}")
        if not getattr(self.store, "configured", False):
            problems.append("no secret store configured (VAULT_ADDR/VAULT_TOKEN): an issued private key could not be kept")
        else:
            try:
                self.store.check()
            except SecretStoreError as e:
                problems.append(str(e))
        directory_problem = self._directory_problem()
        if directory_problem:
            problems.append(directory_problem)
        if "staging" in self.directory_url or "pebble" in self.directory_url or ":14000" in self.directory_url:
            warnings.append("This is a TEST CA: the issued certificate will not be trusted by browsers.")
        warnings.append("The new certificate is stored in Vault; deploying it to the endpoint is NOT automated.")
        steps = [
            f"Register an ACME account with {self.directory_url} for {self.email or '(no email)'}",
            f"Create an order for: {', '.join(domains) or '(none)'}",
            f"Prove control of each name via HTTP-01 ({settings.ACME_CHALLENGE})",
            "Generate a NEW private key and CSR, finalize the order and download the certificate chain",
            f"Store the key and chain in Vault under {settings.VAULT_KV_MOUNT}/{settings.VAULT_PATH_PREFIX}/<domain>",
            "Record the result in renewal history (the private key is never logged or returned)",
        ]
        return PlanSpec(params={"domains": domains, "directory": self.directory_url}, steps=steps, warnings=warnings,
                        executable=not problems, not_executable_reason="; ".join(problems) or None)

    def _directory_problem(self) -> Optional[str]:
        try:
            r = requests.get(self.directory_url, timeout=8, verify=settings.ACME_VERIFY_TLS)
            r.raise_for_status()
            return None if "newOrder" in r.json() else "ACME directory has no newOrder endpoint"
        except Exception as e:
            return f"ACME directory unreachable: {type(e).__name__}"

    # ---- execute ---------------------------------------------------------------------------------
    def execute(self, cert: models.Certificate, params: Dict[str, Any]) -> ExecResult:
        domains: List[str] = params["domains"]
        if params.get("directory") != self.directory_url:
            return ExecResult(False, action_taken=False, error="ACME directory changed since the plan was made")
        if self.directory_url.rstrip("/") == PRODUCTION_DIRECTORY and not settings.ACME_ALLOW_PRODUCTION:
            return ExecResult(False, action_taken=False, error="production ACME directory is blocked")
        solver = self._solver()
        presented: List[tuple[str, str]] = []
        try:
            account_key = jose.JWKRSA(key=rsa.generate_private_key(public_exponent=65537, key_size=2048))
            net = client.ClientNetwork(account_key, account=None, alg=jose.RS256, user_agent="certops-ai",
                                       verify_ssl=settings.ACME_VERIFY_TLS, timeout=30)
            directory = client.ClientV2.get_directory(self.directory_url, net)
            acme = client.ClientV2(directory, net)
            acme.new_account(messages.NewRegistration.from_data(email=self.email, terms_of_service_agreed=True))

            cert_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            key_pem = cert_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                             serialization.NoEncryption())
            order = acme.new_order(crypto_util.make_csr(key_pem, domains))

            for authz in order.authorizations:
                domain = authz.body.identifier.value
                challb = next((c for c in authz.body.challenges if isinstance(c.chall, challenges.HTTP01)), None)
                if challb is None:
                    return ExecResult(False, error=f"CA offered no HTTP-01 challenge for {domain}")
                response, validation = challb.response_and_validation(acme.net.key)
                token = challb.chall.encode("token")
                solver.present(domain, token, validation)
                presented.append((domain, token))
                acme.answer_challenge(challb, response)

            finalized = acme.poll_and_finalize(order)  # library default: 90 s deadline
            fullchain = finalized.fullchain_pem
            leaf = x509.load_pem_x509_certificates(fullchain.encode())[0]
            serial = format(leaf.serial_number, "x")
            ref = self.store.put(f"{domains[0]}", {
                "private_key": key_pem.decode(), "fullchain": fullchain, "domains": ",".join(domains),
                "serial": serial, "not_after": ensure_utc(leaf.not_valid_after_utc).isoformat(),
                "issued_by": self.directory_url})
            return ExecResult(True, serial_number=serial, not_after=ensure_utc(leaf.not_valid_after_utc),
                              secret_ref=ref, detail={"domains": domains, "ca": self.directory_url})
        except SecretStoreError as e:
            return ExecResult(False, error=f"vault: {e} (the certificate was issued but could not be stored)")
        except Exception as e:  # ACME errors carry the CA's problem document; keep it, it's what RCA reads
            return ExecResult(False, error=f"{type(e).__name__}: {e}"[:600])
        finally:
            for domain, token in presented:
                solver.cleanup(domain, token)
