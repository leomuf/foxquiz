# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Get the project number
data "google_project" "project" {
  project_id = var.project_id
}

locals {
  default_compute_service_account = "${data.google_project.project.number}-compute@developer.gserviceaccount.com"
}

# agents-cli uses the default Compute service account unless another identity is
# passed explicitly. Terraform configures Cloud Run to use the same account and
# grants it the project-level permissions required to build and run FoxQuiz.
resource "google_project_iam_member" "default_compute_sa_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/artifactregistry.writer",
    "roles/cloudbuild.builds.builder",
    "roles/cloudtrace.agent",
    "roles/datastore.user",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/telemetry.tracesWriter",
  ])

  project    = var.project_id
  role       = each.value
  member     = "serviceAccount:${local.default_compute_service_account}"
  depends_on = [resource.google_project_service.services]
}
