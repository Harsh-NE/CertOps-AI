"""
Certificate Ownership Discovery.

Maps a certificate (by common_name / SAN / source_ref) to a responsible
team/owner using three signal sources, in priority order:

  1. Kubernetes annotations on the Secret/Ingress that carries the cert
     (e.g. metadata.annotations["cert-owner/team"])
  2. Git repository metadata - a CODEOWNERS file, or a `.cert-owners.yaml`
     manifest committed alongside service code
  3. Service metadata files (service.yaml / catalog-info.yaml, common in
     Backstage-style service catalogs) with an `owner:` field

Each function is independent and safe to call even when that signal source
isn't configured - they return None/[] rather than raising, so discovery
degrades gracefully.
"""
import os
import re
from typing import Any, Dict, List, Optional

import yaml


def owner_from_k8s_annotations(annotations: Dict[str, str]) -> Optional[Dict[str, Any]]:
    if not annotations:
        return None
    team = annotations.get("cert-owner/team") or annotations.get("owner/team")
    email = annotations.get("cert-owner/email") or annotations.get("owner/email")
    slack = annotations.get("cert-owner/slack") or annotations.get("owner/slack-channel")
    if not (team or email):
        return None
    return {
        "name": team or email,
        "team": team,
        "email": email,
        "slack_channel": slack,
        "source": "k8s_annotation",
    }


def owner_from_git_codeowners(repo_path: str, file_glob_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Parses a CODEOWNERS file (GitHub/GitLab standard location) and returns the
    first matching owner entry. If file_glob_hint is given (e.g. a path like
    'infra/certs/api.lab.local'), tries to match the most specific pattern;
    otherwise returns the last (default/catch-all) rule, which is CODEOWNERS
    convention for "everything else".
    """
    candidates = [
        os.path.join(repo_path, "CODEOWNERS"),
        os.path.join(repo_path, ".github", "CODEOWNERS"),
        os.path.join(repo_path, "docs", "CODEOWNERS"),
    ]
    codeowners_path = next((p for p in candidates if os.path.isfile(p)), None)
    if not codeowners_path:
        return None

    rules: List[tuple] = []
    with open(codeowners_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            pattern, owners = parts[0], parts[1:]
            rules.append((pattern, owners))

    if not rules:
        return None

    match = None
    if file_glob_hint:
        for pattern, owners in rules:
            regex = re.escape(pattern).replace(r"\*", ".*")
            if re.search(regex, file_glob_hint):
                match = (pattern, owners)
    if match is None:
        match = rules[-1]  # CODEOWNERS convention: last matching/default rule

    owners_str = ", ".join(match[1])
    return {
        "name": owners_str,
        "team": match[1][0].lstrip("@"),
        "email": None,
        "slack_channel": None,
        "source": "git_codeowners",
    }


def owner_from_service_metadata(repo_path: str) -> Optional[Dict[str, Any]]:
    """
    Looks for a Backstage-style catalog-info.yaml or a lightweight
    service.yaml / .cert-owners.yaml with an `owner:` field.
    """
    candidates = [
        os.path.join(repo_path, "catalog-info.yaml"),
        os.path.join(repo_path, "service.yaml"),
        os.path.join(repo_path, ".cert-owners.yaml"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                doc = yaml.safe_load(f)
        except Exception:
            continue
        if not doc:
            continue
        # Backstage catalog-info.yaml shape
        owner = (
            doc.get("spec", {}).get("owner")
            if isinstance(doc.get("spec"), dict) else None
        ) or doc.get("owner")
        if owner:
            return {
                "name": owner,
                "team": owner,
                "email": doc.get("email"),
                "slack_channel": doc.get("slack_channel"),
                "source": "service_metadata",
            }
    return None


def resolve_owner(
    k8s_annotations: Optional[Dict[str, str]] = None,
    repo_path: Optional[str] = None,
    file_glob_hint: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Priority: k8s annotations > git CODEOWNERS > service metadata."""
    if k8s_annotations:
        found = owner_from_k8s_annotations(k8s_annotations)
        if found:
            return found
    if repo_path and os.path.isdir(repo_path):
        found = owner_from_git_codeowners(repo_path, file_glob_hint)
        if found:
            return found
        found = owner_from_service_metadata(repo_path)
        if found:
            return found
    return None
