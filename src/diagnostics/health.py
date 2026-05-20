import os
import logging
import pathlib
import boto3
from typing import Dict, List, Tuple

# We import the global instances to check connectivity
from src.analytics.rds_client import rds_analytics

logger = logging.getLogger(__name__)

class HealthChecker:
    """System Diagnostic Tool for Project Asha.
    
    Verifies environment, assets, database, and cloud connectivity.
    """

    PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
    ASSETS_DIR = PROJECT_ROOT / "assets"
    
    REQUIRED_ENV = [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "ENCRYPTION_KEY"
    ]
    
    REQUIRED_PCM_ASSETS = [
        "hello.pcm",
        "emergency.pcm",
        "transfer.pcm"
    ]

    @classmethod
    def check_env(cls) -> Dict[str, bool]:
        """Verify presence of critical environment variables."""
        results = {}
        for var in cls.REQUIRED_ENV:
            val = os.environ.get(var)
            results[var] = bool(val and "your_" not in val.lower())
        return results

    @classmethod
    def check_assets(cls) -> Dict[str, bool]:
        """Verify presence of PCM audio assets."""
        results = {}
        for asset in cls.REQUIRED_PCM_ASSETS:
            path = cls.ASSETS_DIR / asset
            results[asset] = path.exists() and path.stat().st_size > 0
        return results

    @classmethod
    def check_database(cls) -> Tuple[bool, str]:
        """Test database connectivity."""
        try:
            conn = rds_analytics.get_connection()
            if not conn:
                return False, "Connection failed"
            
            is_sqlite = rds_analytics.is_sqlite(conn)
            conn.close()
            return True, "SQLite (Demo)" if is_sqlite else "Postgres (RDS)"
        except Exception as e:
            return False, str(e)

    @classmethod
    def check_aws(cls) -> Tuple[bool, str]:
        """Verify AWS credentials and Bedrock access."""
        from botocore.config import Config
        config = Config(connect_timeout=2.0, read_timeout=2.0, retries={'max_attempts': 0})
        try:
            # 1. Identity Check
            sts = boto3.client('sts', config=config)
            sts.get_caller_identity()
            
            # 2. Bedrock Access Check (Nova Mini check)
            # [MED FIX] Actually invoke Bedrock to verify IAM allows Bedrock actions
            bedrock = boto3.client('bedrock', region_name=os.environ.get("BEDROCK_REGION", "us-east-1"), config=config)
            bedrock.list_foundation_models(byProvider="Amazon")
            
            return True, "Connected (IAM & Bedrock Validated)"
        except Exception as e:
            return False, f"AWS Error: {str(e)}"

    @classmethod
    def run_full_diagnostic(cls) -> Dict:
        """Run all checks and return a structured report."""
        report = {
            "environment": cls.check_env(),
            "assets": cls.check_assets(),
            "database": cls.check_database(),
            "aws": cls.check_aws(),
            # [LOW FIX] Use actual current timestamp
            "timestamp": __import__("datetime").datetime.now().isoformat()
        }
        
        # Summary status
        env_ok = all(report["environment"].values())
        assets_ok = all(report["assets"].values())
        db_ok = report["database"][0]
        aws_ok = report["aws"][0]
        
        report["overall_status"] = "HEALTHY" if all([env_ok, assets_ok, db_ok, aws_ok]) else "DEGRADED"
        return report

if __name__ == "__main__":
    # Setup simple logging for standalone run
    logging.basicConfig(level=logging.INFO)
    print("\n" + "="*50)
    print("PROJECT ASHA: SYSTEM DIAGNOSTICS")
    print("="*50)
    
    checker = HealthChecker()
    diag = checker.run_full_diagnostic()
    
    print(f"\nOVERALL STATUS: {diag['overall_status']}")
    
    print("\n[ENV VARIABLES]")
    for k, v in diag["environment"].items():
        print(f"  [{'OK' if v else '!!'}] {k}")
        
    print("\n[AUDIO ASSETS]")
    for k, v in diag["assets"].items():
        print(f"  [{'OK' if v else '!!'}] {k}")
        
    print("\n[DATABASE]")
    ok, msg = diag["database"]
    print(f"  [{'OK' if ok else '!!'}] Status: {msg}")
    
    print("\n[AWS CLOUD]")
    ok, msg = diag["aws"]
    print(f"  [{'OK' if ok else '!!'}] Status: {msg}")
    print("="*50 + "\n")
