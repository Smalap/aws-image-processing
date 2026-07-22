# Serverless Image Processing System — Build Guide (AWS Console)

**Region used throughout:** `us-east-1` (N. Virginia). Pick one region and never switch — most "it doesn't work" problems are a region mismatch.

**Time:** ~60–75 minutes first time.
**Cost:** ~₹0 if you stay in Free Tier and delete the buckets when done.

---

## Naming convention

| Thing | Name |
|---|---|
| Source bucket | `shivam-images-source-2026` |
| Destination bucket | `shivam-images-processed-2026` |
| IAM role | `image-processor-lambda-role` |
| IAM policy | `image-processor-policy` |
| Lambda function | `image-processor` |
| Lambda layer | `pillow-layer` |

S3 bucket names are globally unique across all AWS customers, so add a suffix (here `2026`).

---

## Phase 0 — Before you touch the console

```bash
aws sts get-caller-identity     # prints your Account ID + user ARN
aws configure get region        # confirm your region
python --version                # need 3.9+
```

---

## Phase 1 — Create the two S3 buckets

Create `shivam-images-source-2026` and `shivam-images-processed-2026`. For each:
Block Public Access — leave all four boxes checked. Everything else default.

> ### The #1 trap
> These must be **two different buckets**. If Lambda writes the processed image back into the source bucket, that write fires another S3 event, invoking Lambda again — forever. This is a recursive invocation loop and it can run up a large bill. Two buckets make it impossible.

---

## Phase 2 — IAM role for Lambda

### 2.1 Create the policy

IAM → Policies → Create policy → JSON tab → paste `iam/lambda-policy.json`
(bucket names already filled in) → name it `image-processor-policy`.

> Note the `/*` on the bucket ARNs. `arn:aws:s3:::bucket` is the bucket itself;
> `arn:aws:s3:::bucket/*` is the objects inside it. Object actions need the `/*` form.

### 2.2 Create the role

IAM → Roles → Create role → Trusted entity **AWS service** → Use case **Lambda** →
attach `image-processor-policy` → name it `image-processor-lambda-role`.

---

## Phase 3 — Build the Pillow layer

Lambda's Python does not include Pillow, and a Windows-built wheel crashes in Lambda's
Linux runtime. Force the Linux wheel:

```powershell
mkdir pillow-build; cd pillow-build; mkdir python
pip install --platform manylinux2014_x86_64 --target=python --implementation cp --python-version 3.12 --only-binary=:all: Pillow
Compress-Archive -Path python -DestinationPath pillow-layer.zip
```

(Or run `scripts/build-layer.sh` on macOS/Linux/WSL.)

The zip must contain `python/PIL/...` at its root — that's the layer contract.

Upload: Lambda → Layers → Create layer → name `pillow-layer`, upload the zip,
architecture `x86_64`, runtime `Python 3.12`.

---

## Phase 4 — Create the Lambda function

Lambda → Create function → Author from scratch → name `image-processor`,
runtime **Python 3.12**, architecture **x86_64**.
Under Additional settings, turn on **Custom execution role** and select
`image-processor-lambda-role`.

- **Code:** paste `src/lambda_function.py`, then **Deploy**.
- **Layer:** scroll to Layers → Add a layer → Custom layers → `pillow-layer` v1.
- **Env vars** (Configuration → Environment variables):
  `DEST_BUCKET=shivam-images-processed-2026`, `MAX_WIDTH=800`, `MAX_HEIGHT=800`,
  `JPEG_QUALITY=80`, `OUTPUT_FORMAT=keep`.
- **General config:** Memory **512 MB**, Timeout **30 s**.

> Lambda allocates CPU in proportion to memory. 512 MB gives ~4× the CPU of 128 MB;
> since resizing is CPU-bound, the higher setting often finishes fast enough to cost
> the same or less. Tune against `Max Memory Used` in the CloudWatch REPORT line.

---

## Phase 5 — Wire up the S3 trigger

On the function → Add trigger → S3 → Bucket **`shivam-images-source-2026`** (the SOURCE) →
event type **All object create events** → acknowledge the recursive-invocation box → Add.

---

## Phase 6 — Test

Upload an image to the source bucket:

```bash
aws s3 cp test.jpg s3://shivam-images-source-2026/
```

Check `shivam-images-processed-2026` for the resized copy, then read the logs:

```bash
aws logs tail /aws/lambda/image-processor --follow
```

Expected:

```
Processed test.jpg -> s3://shivam-images-processed-2026/test.jpg | 4032x3024 -> 800x600 | 3847221 B -> 84102 B (97.8% smaller)
```

---

## Phase 7 — CloudWatch housekeeping

- Log group `/aws/lambda/image-processor` → Actions → Edit retention → **7 days**
  (default is Never expire, which costs money forever).
- Optionally add an alarm on the Lambda **Errors** metric (Sum > 0) with an SNS email.

---

## Phase 8 — Push to GitHub

```bash
git init
git add .
git status      # confirm no zips, no python/ folder, no credentials
git commit -m "Serverless image processing pipeline: S3 + Lambda + CloudWatch"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/aws-image-processing.git
git push -u origin main
```

GitHub scans public repos for AWS keys and will quarantine your account if it finds one.
The included `.gitignore` keeps keys and the layer build out of the commit.

---

## Phase 9 — Tear down (avoid charges)

Delete in order: Lambda function → layer → empty and delete both buckets →
CloudWatch log group and alarm → IAM role, then policy.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No module named 'PIL'` | Layer not attached, or zip structure wrong | Zip must have `python/PIL/...` at root |
| `No module named 'PIL._imaging'` | Wheel built on Windows/macOS | Rebuild with `--platform manylinux2014_x86_64 --only-binary=:all:` |
| `AccessDenied` on GetObject | Policy ARN missing `/*` or wrong bucket | Check ARNs in `image-processor-policy` |
| `Task timed out after 3.00 seconds` | Default timeout | Raise to 30 s |
| Lambda never fires | Trigger on wrong bucket / wrong region | Trigger must be on the source bucket |
| Lambda fires infinitely | Source and destination are the same bucket | Disable the trigger, use two buckets |
| `KeyError: 'DEST_BUCKET'` | Env var not set/saved | Configuration → Environment variables |

---

## Interview questions this project sets you up for

**What triggers the Lambda?** An S3 event notification (`s3:ObjectCreated:*`) from the
source bucket. S3 pushes the event; a resource-based policy on the function permits S3 to invoke it.

**Why Lambda over EC2?** No idle cost, no servers to patch, and it scales with concurrent
uploads. Image processing is bursty and short-lived — Lambda's ideal shape.

**What happens on failure?** S3 invokes Lambda asynchronously, so it retries twice with
backoff, then drops the event unless a dead-letter queue / on-failure destination is set.

**Why two buckets?** To prevent recursive invocation — a single bucket re-triggers the
function on its own output.

**How would you improve it?** Multiple output sizes in one pass; SQS between S3 and Lambda
for batching + DLQ; DynamoDB for metadata; CloudFront for delivery; WebP/AVIF output.
