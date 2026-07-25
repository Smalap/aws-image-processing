# Project Description & Resume Content — Serverless Image Processing Pipeline

## Full description (~700 words)

I built a serverless, event-driven image processing pipeline on AWS to understand how cloud
services compose into a real production system, without ever running or managing a server.
The idea is simple to describe but touches almost every core AWS building block: a user
uploads an image to an S3 bucket, that upload automatically triggers a Lambda function, the
function resizes and compresses the image using the Pillow library, and the result is written
to a second S3 bucket — all while every step is logged to CloudWatch for visibility and
debugging.

The architecture deliberately uses two separate S3 buckets rather than one. The source bucket
only receives raw uploads and is the sole trigger for the pipeline; the destination bucket only
receives processed output and has no trigger attached to it at all. This isn't a stylistic
choice — it's a structural safeguard. If Lambda wrote its output back into the same bucket it
was watching, that write would itself count as an "object created" event, which would invoke
Lambda again, which would write again, and so on indefinitely. This is called a recursive
invocation loop, and because Lambda bills per invocation, an unnoticed loop can run up real
cost overnight. Separating the buckets makes the loop structurally impossible rather than
relying on application logic to prevent it, though I also added a guard clause in the code
itself that raises an error if the two buckets are ever misconfigured to be the same.

Permissions were the part that taught me the most. Rather than attaching a broad managed policy
like AmazonS3FullAccess, I wrote a custom least-privilege IAM policy that grants the Lambda
function exactly three things: permission to read objects from the source bucket, permission to
write objects to the destination bucket, and permission to write its own logs to CloudWatch.
Nothing else. Getting the ARNs right taught me the distinction between a bucket resource
(`arn:aws:s3:::bucket-name`) and the objects inside it (`arn:aws:s3:::bucket-name/*`) — a
subtle but common source of AccessDenied errors, since object-level actions like GetObject and
PutObject require the `/*` suffix while bucket-level actions don't.

The trickiest technical hurdle was Lambda layers. AWS Lambda's Python runtime doesn't ship with
Pillow, and installing it locally on Windows produces compiled binaries that crash inside
Lambda's Linux execution environment with an error like `No module named 'PIL._imaging'`. The
fix was to use pip's `--platform manylinux2014_x86_64 --only-binary=:all:` flags to force
download of the Linux-compatible wheel regardless of the machine building it, then package that
into a zip with a specific folder structure (`python/` at the root) that matches the contract
Lambda layers expect, since Lambda unpacks layers into `/opt` and Python looks for modules under
`/opt/python`.

I also learned that Lambda's memory setting isn't just about avoiding out-of-memory errors —
Lambda allocates CPU proportionally to memory, so for CPU-bound work like image resizing,
raising memory from the 128 MB default to 512 MB measurably reduced execution duration. Since
Lambda bills as memory × duration, a faster function at higher memory can end up costing the
same or less than a slower function at lower memory. I confirmed this directly by comparing the
`Billed Duration` and `Max Memory Used` fields in the CloudWatch REPORT log line for each run.

Finally, I set up a GitHub Actions CI/CD pipeline that lints and import-checks the code on every
pull request, then packages and deploys the Lambda function automatically on every push to
main. Authentication uses GitHub's OIDC identity provider rather than long-lived AWS access
keys stored as secrets — GitHub requests a short-lived token from AWS STS at pipeline run time,
which removes the risk of a leaked long-term credential entirely. The finished project,
including the Lambda source, IAM policy, setup documentation, and workflow file, is published
on GitHub as a portfolio piece.

---

## What I actually learned (skills to speak to in an interview)

- **Event-driven architecture** — designing a system around an event trigger (S3
  ObjectCreated) instead of a request/response API, and understanding that S3 invokes Lambda
  *asynchronously*: no caller waits for a response, and failures retry automatically before
  being dropped.
- **AWS Lambda fundamentals** — runtimes, handlers, environment variables, memory/timeout
  tuning, and the relationship between memory allocation and CPU allocation.
- **Lambda Layers** — packaging third-party dependencies (Pillow) separately from function
  code, and the cross-platform build problem of compiled binaries (Linux wheel vs. Windows
  wheel) that only shows up once code actually runs in the cloud.
- **IAM least-privilege policy design** — writing scoped JSON policies by hand instead of
  attaching broad managed policies, and understanding S3 ARN resource patterns (bucket vs.
  object-level permissions).
- **S3 event notifications** — configuring bucket-level triggers, understanding the
  resource-based policy AWS auto-attaches to allow S3 to invoke a function.
- **CloudWatch observability** — reading structured logs to verify behavior, using the REPORT
  line (Duration, Billed Duration, Memory Size, Max Memory Used) to make a cost/performance
  tuning decision rather than guessing.
- **Recursive invocation loops** — recognizing a class of bug specific to event-driven cloud
  systems that doesn't exist in traditional request/response applications, and designing the
  architecture to make it structurally impossible.
- **CI/CD for serverless** — GitHub Actions workflow with a test gate before deploy, and
  passwordless AWS authentication via OIDC instead of stored access keys.
- **Documentation and version control hygiene** — structuring a repository (`src/`, `iam/`,
  `docs/`, `tests/`, `scripts/`), writing a `.gitignore` that keeps secrets and large rebuildable
  artifacts out of version control, and writing setup docs a stranger could follow.

---

## Resume bullet points

Use one of these, or combine ideas — pick the length that fits your resume's format.

**One-line version (matches the style of your other AWS project):**

> Built and deployed a serverless image processing pipeline on AWS (S3, Lambda, CloudWatch, IAM) that automatically resizes and compresses uploaded images, secured via least-privilege IAM and automated deployment through a GitHub Actions CI/CD pipeline.

**Two-line version, with a metric:**

> Designed and deployed an event-driven image processing system using S3, Lambda, and CloudWatch that automatically resizes/compresses images on upload, reducing file size by ~95% while preserving image quality.
> Implemented least-privilege IAM policies and a GitHub Actions CI/CD pipeline with OIDC-based authentication (no stored AWS credentials) for automated deployment.

**Project section entry (for a projects page or portfolio, slightly longer):**

> **Serverless Image Processing Pipeline** — AWS S3, Lambda, CloudWatch, IAM, GitHub Actions
> Built an event-driven pipeline where images uploaded to S3 automatically trigger a Python
> Lambda function (using Pillow) that resizes, compresses, and corrects EXIF orientation before
> storing the result in a separate destination bucket. Designed a least-privilege IAM policy,
> built a custom Lambda layer to package Pillow's Linux-compiled dependencies, and configured
> CloudWatch logging for full execution visibility. Automated deployment with a GitHub Actions
> CI/CD workflow using OIDC federation instead of long-lived credentials.
> [github.com/Smalap/aws-image-processing]

---

## Likely interview questions (quick answers you can expand on)

**Why two S3 buckets instead of one?**
Prevents a recursive invocation loop — writing output back to the trigger bucket would cause
Lambda to invoke itself indefinitely.

**Why Lambda instead of a server?**
No idle cost, automatic scaling with upload volume, and no patching/maintenance — a good fit
for short, bursty workloads like image processing.

**How did you handle dependencies Lambda doesn't include?**
Built a Lambda layer containing the Linux-compiled Pillow wheel, since compiled Python packages
built on Windows/macOS aren't binary-compatible with Lambda's Linux runtime.

**How does your CI/CD pipeline authenticate to AWS?**
Via GitHub's OIDC provider — the workflow requests a short-lived token from AWS STS at run
time instead of using stored access keys, removing the risk of a leaked long-term credential.

**How would you scale or improve this?**
Add an SQS queue between S3 and Lambda for batching and a dead-letter queue on failures, store
image metadata in DynamoDB, serve results through CloudFront, and generate multiple output
sizes (thumbnail/medium/large) in a single invocation.
