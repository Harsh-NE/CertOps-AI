"""Minimal local OCSP responder for tests and the lab (not for production)."""
import datetime as dt
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509 import ocsp

from app.devtools.pki import CA


class OcspResponder:
    """Serves signed OCSP responses for certificates issued by `ca`. `revoked` is a set of serial numbers."""

    def __init__(self, ca: CA, revoked=None, tamper: bool = False, bind: str = "127.0.0.1",
                 advertise_host: str = "127.0.0.1"):
        self.ca = ca
        self.revoked = set(revoked or [])
        self.tamper = tamper  # corrupt the signature, to test response verification
        self.requests = 0
        responder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                responder.requests += 1
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                data = responder.respond(body)
                self.send_response(200)
                self.send_header("Content-Type", "application/ocsp-response")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = HTTPServer((bind, 0), Handler)
        self.url = f"http://{advertise_host}:{self.server.server_port}/ocsp"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def respond(self, body: bytes) -> bytes:
        req = ocsp.load_der_ocsp_request(body)
        now = dt.datetime.now(dt.UTC)
        revoked = req.serial_number in self.revoked
        status = ocsp.OCSPCertStatus.REVOKED if revoked else ocsp.OCSPCertStatus.GOOD
        builder = ocsp.OCSPResponseBuilder().add_response_by_hash(
            issuer_name_hash=req.issuer_name_hash, issuer_key_hash=req.issuer_key_hash,
            serial_number=req.serial_number, algorithm=req.hash_algorithm, cert_status=status,
            this_update=now - dt.timedelta(minutes=1), next_update=now + dt.timedelta(hours=1),
            revocation_time=now - dt.timedelta(days=1) if revoked else None,
            revocation_reason=x509.ReasonFlags.key_compromise if revoked else None,
        ).responder_id(ocsp.OCSPResponderEncoding.HASH, self.ca.cert)
        der = builder.sign(self.ca.key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)
        if self.tamper:  # corrupt the trailing signature bytes: still parses, must fail verification
            der = der[:-1] + bytes([der[-1] ^ 0xFF])
        return der

    def close(self):
        self.server.shutdown()
        self.server.server_close()

