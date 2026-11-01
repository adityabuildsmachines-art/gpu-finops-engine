"""Small, fail-closed credential store for the simulation API."""
import hashlib
import hmac
import json
import os
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset[str]
    scopes: frozenset[tuple[str, str]]

    def permits(self, role: str, team: str, workload: str) -> bool:
        return role in self.roles and (team, workload) in self.scopes


def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    try:
        entries = json.loads(os.environ.get("GPU_FINOPS_PRINCIPALS", "[]"))
        if not isinstance(entries, list) or not entries:
            raise ValueError("no principals configured")
        configured = []
        seen = set()
        for entry in entries:
            token, subject = entry["token"], entry["subject"]
            roles, scopes = entry["roles"], entry["scopes"]
            if (not isinstance(token, str) or len(token) < 32 or
                not isinstance(subject, str) or not subject.strip() or
                not isinstance(roles, list) or not roles or
                any(role not in ("evaluate", "read", "approve") for role in roles) or
                not isinstance(scopes, list) or not scopes):
                raise ValueError("invalid principal")
            pairs = []
            for scope in scopes:
                team, workload = scope["team_name"], scope["workload_id"]
                if not isinstance(team, str) or not team.strip() or not isinstance(workload, str) or not workload.strip():
                    raise ValueError("invalid scope")
                pairs.append((team, workload))
            digest = hashlib.sha256(token.encode()).digest()
            if digest in seen:
                raise ValueError("duplicate token")
            seen.add(digest)
            configured.append((digest, Principal(subject, frozenset(roles), frozenset(pairs))))
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
        raise HTTPException(status_code=503, detail="Authentication is not configured") from None

    if credentials is not None:
        supplied = hashlib.sha256(credentials.credentials.encode()).digest()
        for digest, principal in configured:
            if hmac.compare_digest(digest, supplied):
                return principal
    raise HTTPException(status_code=401, detail="Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
