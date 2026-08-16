"""
Convenience script: scans the lab nginx targets (see /lab) and populates the
database in one go, so the dashboard has realistic data immediately after
setup. Requires the lab environment to be running:
    cd lab && ./generate_certs.sh && docker compose -f docker-compose.lab.yml up -d

Usage (from backend/, with venv active):
    python seed_lab_data.py
"""
from app.database import SessionLocal, Base, engine
from app.services.pipeline import scan_and_process_network_target
from app.services.cert_scanner import CertScanError

Base.metadata.create_all(bind=engine)

LAB_TARGETS = [
    "app1.lab.local:8443",
    "app2.lab.local:8443",
    "app3.lab.local:8443",
    "legacy.lab.local:8443",
    "api.lab.local:8443",
]

if __name__ == "__main__":
    db = SessionLocal()
    for target in LAB_TARGETS:
        try:
            cert = scan_and_process_network_target(db, target)
            print(f"OK   {target:30s} -> status={cert.status.value:15s} risk={cert.risk_tier.value if cert.risk_tier else '-'}")
        except CertScanError as e:
            print(f"FAIL {target:30s} -> {e}")
    db.close()
    print("\nDone. Add the lab hostnames to /etc/hosts if you haven't:")
    print("  127.0.0.1 app1.lab.local app2.lab.local app3.lab.local legacy.lab.local api.lab.local")
