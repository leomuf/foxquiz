# FoxQuiz Deployment Script

## Status

Implemented as `scripts/deploy.sh`. The script is the required entry point for
manual DEV and production Cloud Run deployments from Linux or WSL.

## Interface

The default mode is a non-mutating preview:

```bash
scripts/deploy.sh --environment dev --project GCLOUD_PROJECT_ID
scripts/deploy.sh --environment prod --project GCLOUD_PROJECT_ID
```

Cloud changes require `--apply` and the environment-specific typed
confirmation:

```bash
scripts/deploy.sh --environment dev --project GCLOUD_PROJECT_ID --apply
scripts/deploy.sh --environment prod --project GCLOUD_PROJECT_ID --apply
```

An existing temporary DEV campaign can be updated without changing its URL:

```bash
scripts/deploy.sh \
  --environment dev \
  --project GCLOUD_PROJECT_ID \
  --service-name RANDOM_DEV_SERVICE_NAME \
  --apply
```

Production always targets `foxquiz`; `--service-name` is rejected for
production. A new DEV deployment generates an unguessable `svc-...` name when
the option is omitted.

## Owned Deployment Sequence

The script performs these operations in order:

1. Validates the environment, project ID, repository, tools, and target name.
2. Refuses an applied deployment from a dirty worktree.
3. Generates the version, full commit SHA, branch, and UTC build timestamp.
4. Selects the fixed environment configuration:
   - production: service `foxquiz`, production identity, `(default)` Firestore,
     and zero-to-ten instance scaling;
   - DEV: random or explicitly reused service, DEV identity, `foxquiz-dev`, and
     zero-to-two instance scaling.
5. Prints the complete command plan. Without `--apply`, execution ends here.
6. Confirms an active Google account and the selected runtime identity.
7. Requires the operator to type `DEPLOY DEV` or `DEPLOY PROD`.
8. Runs `agents-cli deploy` with explicit CPU, memory, concurrency, scaling,
   service account, database, A2A setting, content-capture setting, and build
   metadata.
9. Applies startup CPU boost and the Gen1 execution environment.
10. Grants `allUsers` the Cloud Run Invoker role.
11. Verifies the Cloud Run identity, scaling annotations, startup settings,
    required environment variables, public IAM policy, root page, and exact
    `/version` metadata.

Every command runs in the foreground under `set -euo pipefail`; a failure stops
the sequence. The script does not create Git tags or GitHub releases.

## Safety Boundary

`scripts/provision-runtime-identities.sh` remains a separate, one-time IAM
operation. The deployment script confirms that its selected account exists but
does not create service accounts or grant project roles.

The deployment script also does not create Firestore databases, indexes, Time
To Live policies, log-based metrics, or domain mappings. Those are one-time
infrastructure operations documented in `CONTRIBUTING.md`.

DEV deletion remains manual and separately confirmed so deployment cannot
accidentally remove an existing campaign. Production deployment remains a
separately approved operation after DEV verification.

## Verification and Testing

Credential-free tests exercise DEV and production previews, reject unsafe
arguments, and run the full applied DEV sequence against fake `agents-cli`,
`gcloud`, and `curl` commands. A real deployment is tested only on DEV after
explicit human approval; production is never deployed as part of automated
testing.

## Possible Future Automation

A later GitHub Actions workflow could call the same script with workload
identity federation and an approval-protected production environment. That is
outside the current manual deployment scope.
