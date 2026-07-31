# Contributing to FoxQuiz 🦊

First off, thank you for checking out FoxQuiz! We are thrilled that you want to help us build a more engaging, gamified, and child-safe learning companion for kids around the world. 

FoxQuiz is developed and maintained by **AUTOSOFT Engineering** and is now open to public contributions. By contributing to this project, you help make education more interactive, personalized, and accessible.

---

## ⚖️ Licensing & Intellectual Property

By contributing your code, documentation, or other materials to FoxQuiz, you agree that:
1. Your contributions will be licensed under the project's **Creative Commons Attribution 4.0 International (CC BY 4.0)** license.
2. Any upstream components belonging to Google LLC's Agent Development Kit (ADK) remain preserved under their respective **Apache License 2.0**.
3. You grant AUTOSOFT Engineering and the public the right to use, modify, and distribute your contributions freely under these terms.

---

## 🚀 How to Get Started

### 1. Reporting Bugs & Requesting Features
If you find a bug or have a great idea for a new mascot, educational topic, or UI feature:
* Check the existing [GitHub Issues](https://github.com/leomuf/foxquiz/issues) to see if it has already been reported.
* If not, open a new issue with a clear description, steps to reproduce, and expected behavior.

### 2. Local Development Setup
To work on the codebase locally, we use `uv` (a fast Python package installer and resolver) to manage dependencies.

1. **Fork and Clone the Repository:**
   ```bash
   git clone https://github.com/your-username/foxquiz.git
   cd foxquiz
   ```

2. **Install Dependencies:**
   Make sure you have [uv](https://github.com/astral-sh/uv) installed, then run:
   ```bash
   uv sync
   ```

3. **Configure Environment Variables:**
   Copy the example environment file and fill in your credentials:
   ```bash
   cp .env.example .env
   ```
   *Edit the `.env` file to add your `GOOGLE_CLOUD_PROJECT` or test variables.*

4. **Run the Application locally:**
   ```bash
   uv run uvicorn app.fast_api_app:app --reload
   ```

### 3. Running Quality Checks
Before submitting your changes, please ensure that all tests and code quality checks pass.

* **Run unit and integration tests:**
  ```bash
  uv run python -m pytest tests/unit tests/integration
  ```
* **Run the code linter:**
  ```bash
  agents-cli lint
  ```

### 4. Deploying & Infrastructure Optimization (For Maintainers)
If you are deploying updates to Google Cloud Run, follow this workflow to package the app and enforce our strict cost-saving and speed optimizations:

1. **Deploy the application container:**
   ```bash
   agents-cli deploy --no-confirm-project
   ```

2. **Enforce Cost-Savings & Startup Speed Boosts:**
   Because standard deployments can reset the minimum instance counts, you must run this `gcloud` command to lock in our zero-standby billing model and cold start acceleration:
   ```bash
   gcloud run services update foxquiz \
     --project <YOUR_PROJECT_ID> \
     --region us-east1 \
     --min-instances 0 \
     --cpu-boost \
     --execution-environment gen1
   ```
   * *`--min-instances 0`:* Scales down to 0 instances when idle (0.00 € standing billing).
   * *`--cpu-boost`:* Automatically doubles allocated CPU on container boot to fast-track startup.
   * *`--execution-environment gen1`:* Uses lightweight sandboxed gVisor virtual machines to reduce startup latencies to milliseconds.

3. **Ensure Public Accessibility (Allow Unauthenticated Traffic):**
   If the deployment resets the IAM policies, resulting in a `Forbidden` error for anonymous web users, restore public access instantly:
   ```bash
   gcloud run services add-iam-policy-binding foxquiz \
     --member="allUsers" \
     --role="roles/run.invoker" \
     --project <YOUR_PROJECT_ID> \
     --region us-east1
   ```

4. **Manage Custom Domain Subdomains (Optional):**
   To map a subdomain (such as `www.foxquiz.app`) to your Cloud Run service, create the domain mapping in Google Cloud:
   ```bash
   gcloud beta run domain-mappings create \
     --service=foxquiz \
     --domain=www.foxquiz.app \
     --project=<YOUR_PROJECT_ID> \
     --region=us-east1
   ```
   *Note:* Ensure you configure a CNAME record in your domain registrar's DNS settings, pointing `www` to `ghs.googlehosted.com.`.

---

## 📥 Submitting a Pull Request (PR)

When you are ready to share your changes:
1. **Create a new branch** for your feature or bugfix:
   ```bash
   git checkout -b feature/my-amazing-feature
   ```
2. **Commit your changes** with a clear and descriptive commit message. We recommend using semantic messages, e.g., `feat(ui): add new dark mode toggle` or `fix(agent): handle empty topic edge case`.
3. **Push to your fork** and open a **Pull Request** on the main FoxQuiz repository.
4. A maintainer will review your code, run automated tests, and work with you to merge it!

---

## 💬 Community & Support
If you have any questions or want to discuss design decisions, feel free to open a thread in the GitHub Discussions page. We are excited to build the future of EdTech together with you!

*Happy Coding!*  
**The AUTOSOFT Engineering Team**
