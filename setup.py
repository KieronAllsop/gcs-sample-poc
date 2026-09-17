import argparse
import os
import re
import secrets
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TERRAFORM_DIR = ROOT / "terraform_gcp"
DEFAULT_PROJECT = os.environ.get("GCP_PROJECT_ID", "clearkey-video-gcp")
DEFAULT_REGION = os.environ.get("GCP_REGION", "europe-west2")
REQUIRED_SERVICES = [
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "eventarc.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "transcoder.googleapis.com",
]


def run(command, *, capture_output=False, env=None):
    print("+", " ".join(command))
    return subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=capture_output,
        env=env,
    )


def terraform(*arguments, capture_output=False, env=None):
    return run(
        ["terraform", f"-chdir={TERRAFORM_DIR}", *arguments],
        capture_output=capture_output,
        env=env,
    )


def state_addresses(env=None):
    result = terraform("state", "list", capture_output=True, env=env)
    return set(result.stdout.splitlines())


def ensure_state_bucket(state_bucket, project_id, region):
    result = subprocess.run(
        ["gcloud", "storage", "buckets", "describe", f"gs://{state_bucket}"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode == 0:
        return

    run(
        [
            "gcloud",
            "storage",
            "buckets",
            "create",
            f"gs://{state_bucket}",
            "--project",
            project_id,
            "--location",
            region,
            "--uniform-bucket-level-access",
            "--public-access-prevention",
        ]
    )
    run(["gcloud", "storage", "buckets", "update", f"gs://{state_bucket}", "--versioning"])


def enable_required_services(project_id):
    run(["gcloud", "services", "enable", *REQUIRED_SERVICES, "--project", project_id])


def existing_secret_value(project_id, secret_id):
    result = subprocess.run(
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            "--secret",
            secret_id,
            "--project",
            project_id,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode == 0 and result.stdout:
        return result.stdout.rstrip("\r\n")
    return None


def remote_resource_exists(address, resource_id, project_id, region):
    if address.startswith("google_storage_bucket."):
        command = ["gcloud", "storage", "buckets", "describe", f"gs://{resource_id}"]
    elif address.startswith("google_service_account."):
        command = ["gcloud", "iam", "service-accounts", "describe", resource_id.split("/")[-1], "--project", project_id]
    elif address == "google_artifact_registry_repository.clearkey":
        command = ["gcloud", "artifacts", "repositories", "describe", "clearkey", "--location", region, "--project", project_id]
    elif address.startswith("google_sql_database_instance."):
        command = ["gcloud", "sql", "instances", "describe", "clearkey-license-db", "--project", project_id]
    elif address.startswith("google_sql_database."):
        command = ["gcloud", "sql", "databases", "describe", "license_db", "--instance", "clearkey-license-db", "--project", project_id]
    elif address.startswith("google_sql_user."):
        command = ["gcloud", "sql", "users", "list", "--instance", "clearkey-license-db", "--project", project_id, "--filter", "name=db_admin", "--format", "value(name)"]
    elif address.startswith("google_cloud_run_v2_service."):
        service = resource_id.rsplit("/", 1)[-1]
        command = ["gcloud", "run", "services", "describe", service, "--region", region, "--project", project_id]
    elif address.startswith("google_eventarc_trigger."):
        trigger = resource_id.rsplit("/", 1)[-1]
        command = ["gcloud", "eventarc", "triggers", "describe", trigger, "--location", region, "--project", project_id]
    elif address.startswith("google_secret_manager_secret."):
        secret = resource_id.rsplit("/", 1)[-1]
        command = ["gcloud", "secrets", "describe", secret, "--project", project_id]
    else:
        return False

    result = subprocess.run(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.returncode == 0 and (address != "google_sql_user.db_admin" or bool(result.stdout.strip()))


def import_existing_resources(project_id, region, source_bucket, egress_bucket, env):
    project_number = run(
        [
            "gcloud",
            "projects",
            "describe",
            project_id,
            "--format=value(projectNumber)",
        ],
        capture_output=True,
    ).stdout.strip()

    resources = {
        "google_storage_bucket.source": source_bucket,
        "google_storage_bucket.egress": egress_bucket,
        "google_service_account.trigger": f"projects/{project_id}/serviceAccounts/clearkey-trigger@{project_id}.iam.gserviceaccount.com",
        "google_service_account.manifest_patcher": f"projects/{project_id}/serviceAccounts/clearkey-manifest-patcher@{project_id}.iam.gserviceaccount.com",
        "google_service_account.license_server": f"projects/{project_id}/serviceAccounts/clearkey-license-server@{project_id}.iam.gserviceaccount.com",
        "google_service_account.terraform_deployer": f"projects/{project_id}/serviceAccounts/clearkey-terraform-deployer@{project_id}.iam.gserviceaccount.com",
        "google_artifact_registry_repository.clearkey": f"projects/{project_id}/locations/{region}/repositories/clearkey",
        "google_sql_database_instance.license_db": f"{project_id}/clearkey-license-db",
        "google_cloud_run_v2_service.license_server": f"projects/{project_id}/locations/{region}/services/license-server",
        "google_cloud_run_v2_service.transcoder_trigger": f"projects/{project_id}/locations/{region}/services/transcoder-trigger",
        "google_cloud_run_v2_service.packager": f"projects/{project_id}/locations/{region}/services/clear-key-packager",
        "google_eventarc_trigger.source_mp4_finalized": f"projects/{project_id}/locations/{region}/triggers/source-mp4-finalized",
        "google_eventarc_trigger.egress_manifest_finalized": f"projects/{project_id}/locations/{region}/triggers/egress-manifest-finalized",
        "google_secret_manager_secret.database_password": f"projects/{project_id}/secrets/database-password",
        "google_secret_manager_secret.clear_key": f"projects/{project_id}/secrets/clear-key",
    }

    current_state = state_addresses(env)
    sql_instance_exists = subprocess.run(
        [
            "gcloud",
            "sql",
            "instances",
            "describe",
            "clearkey-license-db",
            "--project",
            project_id,
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0
    if sql_instance_exists:
        resources.update(
            {
                "google_sql_database.license_db": f"{project_id}/clearkey-license-db/license_db",
                "google_sql_user.db_admin": f"{project_id}/clearkey-license-db/db_admin",
            }
        )

    for address, resource_id in resources.items():
        if address not in current_state and remote_resource_exists(address, resource_id, project_id, region):
            try:
                terraform(
                    "import",
                    "-input=false",
                    "-lock-timeout=60s",
                    address,
                    resource_id,
                    env=env,
                )
            except subprocess.CalledProcessError:
                print(f"Skipping unavailable resource: {address}")

    print(f"Google project number: {project_number}")


def terraform_output(name):
    return terraform("output", "-raw", name, capture_output=True).stdout.strip()


def main():
    parser = argparse.ArgumentParser(
        description="Build and deploy the GCP ClearKey video POC."
    )
    parser.add_argument("--project-id", default=DEFAULT_PROJECT)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--bucket-suffix",
        default=os.environ.get("GCP_BUCKET_SUFFIX"),
        required=os.environ.get("GCP_BUCKET_SUFFIX") is None,
        help="Account-specific suffix used for the globally unique GCS bucket names.",
    )
    parser.add_argument(
        "--deployer-impersonator",
        default=os.environ.get("GCP_DEPLOYER_IMPERSONATOR"),
        help="IAM principal allowed to impersonate the Terraform deployer, for example user:admin@example.com.",
    )
    parser.add_argument(
        "--skip-import",
        action="store_true",
        help="Do not import resources already created outside Terraform.",
    )
    parser.add_argument("--db-password")
    parser.add_argument("--clear-key-value")
    args = parser.parse_args()
    source_bucket = f"clearkey-video-gcp-source-{args.bucket_suffix}"
    egress_bucket = f"clearkey-video-gcp-egress-{args.bucket_suffix}"
    state_bucket = f"clearkey-video-gcp-tfstate-{args.bucket_suffix}"

    run(["gcloud", "config", "set", "project", args.project_id])
    enable_required_services(args.project_id)
    ensure_state_bucket(state_bucket, args.project_id, args.region)
    terraform("init", "-input=false", f"-backend-config=bucket={state_bucket}")

    db_password = (
        args.db_password
        or existing_secret_value(args.project_id, "database-password")
        or secrets.token_urlsafe(32)
    )
    clear_key_value = (
        args.clear_key_value
        or existing_secret_value(args.project_id, "clear-key")
        or secrets.token_hex(16)
    )
    if not re.fullmatch(r"[0-9a-fA-F]{32}", clear_key_value):
        raise SystemExit("ClearKey value must contain exactly 32 hexadecimal characters.")

    terraform_env = os.environ.copy()
    terraform_env["TF_VAR_db_password"] = db_password
    terraform_env["TF_VAR_clear_key_value"] = clear_key_value

    if not args.skip_import:
        import_existing_resources(
            args.project_id, args.region, source_bucket, egress_bucket, terraform_env
        )

    apply_arguments = [
        "apply",
        "-auto-approve",
        "-input=false",
        "-lock-timeout=60s",
        f"-var=project_id={args.project_id}",
        f"-var=region={args.region}",
        f"-var=source_bucket_name={source_bucket}",
        f"-var=egress_bucket_name={egress_bucket}",
    ]
    if args.deployer_impersonator:
        apply_arguments.append(
            f"-var=deployer_impersonator_principal={args.deployer_impersonator}"
        )

    terraform(
        *apply_arguments,
        env=terraform_env,
    )

    print("\nGCP setup complete")
    for output in [
        "license_server_url",
        "transcoder_trigger_url",
        "packager_url",
        "source_bucket",
        "egress_bucket",
    ]:
        print(f"{output}: {terraform_output(output)}")


if __name__ == "__main__":
    main()
