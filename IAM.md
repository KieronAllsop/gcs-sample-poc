# IAM permissions

Terraform is the source of truth for the bindings in `terraform_gcp/main.tf`.
This matrix records why each runtime identity has its grants and keeps deployment
permissions separate from permissions used by the running services.

## Runtime identities

| Identity | Scope | Role or permission | Reason |
| --- | --- | --- | --- |
| `clearkey-trigger` | Project | `clearkeyTranscoderJobSubmitter` (`transcoder.jobs.create`, `get`, `list`) | Checks for an existing job and submits a Transcoder job. |
| `clearkey-trigger` | Project | `roles/eventarc.eventReceiver` | Allows the source Eventarc trigger to deliver events. |
| `clearkey-eventarc-invoker` | Project | `roles/eventarc.eventReceiver` | Allows the egress Eventarc trigger to deliver events. |
| `clearkey-trigger` | Cloud Run service | `roles/run.invoker` on `transcoder-trigger` | Invokes the source Eventarc destination. |
| `clearkey-eventarc-invoker` | Cloud Run service | `roles/run.invoker` on `clear-key-packager` | Invokes the egress Eventarc destination without inheriting Transcoder permissions. |
| Transcoder service agent | Source bucket | `roles/storage.objectViewer` | Reads source media while transcoding. |
| Transcoder service agent | Egress bucket | `roles/storage.objectCreator` | Writes newly transcoded objects; it cannot delete or replace objects. |
| `clearkey-manifest-patcher` | Egress bucket | `clearkeyMediaObjectWriter` (`storage.objects.create`, `delete`, `get`, `list`) | Reads generated media and replaces it with encrypted output. |
| `clearkey-manifest-patcher` | `clear-key` secret | `roles/secretmanager.secretAccessor` | Supplies the packaging key to the container. |
| `clearkey-license-server` | Egress bucket | `roles/storage.objectViewer` | Serves encrypted manifests and media. |
| `clearkey-license-server` | Cloud SQL project | `roles/cloudsql.client` | Connects to the Cloud SQL instance. |
| `clearkey-license-server` | `database-password`, `clear-key` secrets | `roles/secretmanager.secretAccessor` | Supplies database and license-server credentials. |

The Google-managed Eventarc and Storage service agents retain their required
service-agent, bucket-reader, and Pub/Sub-publisher grants in Terraform.

## Deployment identity

`clearkey-terraform-deployer` is intentionally broader because it provisions
infrastructure and IAM. It currently has project-level administration roles for
Service Usage, Storage, Artifact Registry, Cloud Run, Eventarc, Cloud SQL, Secret
Manager, service accounts, project IAM, custom IAM roles, and Cloud Build. Use a
separate bootstrap identity for first deployment and replace this with a CI-only
custom role or resource-level permissions before production. Do not attach this
identity to a Cloud Run service.

The deployer can impersonate the three runtime service accounts through
`roles/iam.serviceAccountUser`; this is only for Terraform to set their runtime
identity. An optional human or CI principal can impersonate the deployer through
`roles/iam.serviceAccountTokenCreator`.

## Production gaps

This project remains a learning POC. Before production, restrict the public
license-server ingress, remove the public `0.0.0.0/0` Cloud SQL network, enforce
TLS or use the Cloud SQL connector, and replace the ClearKey test flow with a
production DRM and authorization design. Those changes are separate from the
runtime IAM reductions above.