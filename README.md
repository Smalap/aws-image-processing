# Serverless Image Processing Pipeline

An event-driven image processing system on AWS. Images uploaded to an S3 bucket are automatically resized and compressed by a Lambda function, then stored in a second bucket. All execution is logged and monitored through CloudWatch.

```
                                    ┌─────────────────┐
                                    │   CloudWatch    │
                                    │  Logs & Alarms  │
                                    └────────▲────────┘
                                             │
┌──────┐   upload   ┌──────────┐  event  ┌───┴────┐  put   ┌──────────────┐
│ User ├───────────►│  S3      ├────────►│ Lambda ├───────►│  S3          │
└──────┘            │  source  │         │ Pillow │        │  destination │
                    └──────────┘         └────────┘        └──────────────┘
```

## What it does

- Watches a source S3 bucket for `ObjectCreated` events
- Resizes images to fit within a configurable bounding box, preserving aspect ratio
- Compresses with quality/optimisation settings per format
- Corrects EXIF orientation so phone photos aren't rotated
- Flattens transparency when converting to JPEG
- Writes the result to a separate destination bucket with source metadata attached
- Logs dimensions, byte sizes, and compression ratio for every image

Typical result: a 3.8 MB phone photo becomes an 84 KB 800px image — roughly 98% smaller.

## Stack

| Service | Role |
|---|---|
| Amazon S3 | Object storage — source and destination buckets |
| AWS Lambda | Serverless compute — Python 3.12 + Pillow |
| Amazon CloudWatch | Logs, metrics, error alarm |
| AWS IAM | Least-privilege execution role |

## Repository layout

```
.
├── src/
│   └── lambda_function.py     # Lambda handler
├── iam/
│   └── lambda-policy.json     # Least-privilege IAM policy
├── docs/
│   └── SETUP-GUIDE.md         # Full console walkthrough
├── scripts/
│   └── build-layer.sh         # Builds the Pillow Lambda layer
├── tests/
│   └── test_event.json        # Sample S3 event for console testing
├── .github/workflows/
│   └── deploy.yml             # CI/CD: deploy to Lambda on push
└── README.md
```

## Configuration

The function is configured entirely through environment variables:

| Variable | Default | Description |
|---|---|---|
| `DEST_BUCKET` | *(required)* | Destination bucket name |
| `MAX_WIDTH` | `800` | Maximum output width in pixels |
| `MAX_HEIGHT` | `800` | Maximum output height in pixels |
| `JPEG_QUALITY` | `80` | JPEG/WebP quality, 1–95 |
| `OUTPUT_FORMAT` | `keep` | `keep`, `jpeg`, `webp`, or `png` |
| `DEST_PREFIX` | `""` | Optional key prefix, e.g. `resized/` |

## Setup

See [`docs/SETUP-GUIDE.md`](docs/SETUP-GUIDE.md) for the full walkthrough. In short:

1. Create two S3 buckets in the same region
2. Create an IAM role using `iam/lambda-policy.json`
3. Build and publish the Pillow layer (`scripts/build-layer.sh`)
4. Create the Lambda function, paste `src/lambda_function.py`, attach the layer
5. Set environment variables; raise memory to 512 MB and timeout to 30 s
6. Add an S3 trigger on the **source** bucket for `s3:ObjectCreated:*`

## Design notes

**Two buckets, not one.** Writing the output back into the source bucket would re-trigger the function on its own output — an infinite invocation loop with a real cost attached. Separate buckets make this structurally impossible; the handler also raises if the two are ever configured identically.

**Least-privilege IAM.** The execution role grants `s3:GetObject` on the source bucket only and `s3:PutObject` on the destination only, rather than the blanket `AmazonS3FullAccess` most tutorials reach for.

**Memory tuning.** Lambda allocates CPU in proportion to memory. Because resizing is CPU-bound, raising memory from 128 MB to 512 MB cuts duration by more than it raises the per-millisecond rate, so the higher setting is frequently *cheaper* per image. `Max Memory Used` in the CloudWatch `REPORT` line is what to tune against.

**Failure handling.** S3 invokes Lambda asynchronously, so failures are retried twice with backoff before the event is dropped. Transient S3 errors are re-raised to let that retry happen; undecodable files are logged and skipped so one bad upload can't fail an entire batch.

## CI/CD

`.github/workflows/deploy.yml` runs lint + an import check on every pull request, and on pushes to `main` packages `src/lambda_function.py` and deploys it to Lambda. Authentication uses GitHub OIDC — a short-lived token from AWS STS — so no long-lived AWS keys are stored in the repository. One-time AWS setup is documented at the bottom of the workflow file.

## Possible extensions

- Generate multiple sizes (thumbnail / medium / large) in a single pass
- Insert SQS between S3 and Lambda for batching and a dead-letter queue
- Record image metadata in DynamoDB
- Serve processed images through CloudFront
- Convert to WebP or AVIF for further bandwidth savings
- Define the whole stack in Terraform or AWS SAM

## Cost

Comfortably within the AWS Free Tier for development use: 1M Lambda requests and 400,000 GB-seconds per month, plus 5 GB of S3 storage. Delete the resources when finished — teardown steps are in the setup guide.

## License

MIT
