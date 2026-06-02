# e2e smoke fixtures

The smoke test (`tests/e2e/smoke-test.sh`) reuses the kernel's bundled sample
documents rather than duplicating multi-MB PDFs here:

| Union | Rate Notice / CBA | Groundtruth |
|---|---|---|
| `sprinkler_fitters_704` | `kernel/data/sprinkler_fitters_704/cba/*.pdf` | `.../ratesheet/*.csv` |
| `pipe_fitters_537` | `kernel/data/pipe_fitters_537/cba/*.pdf` | `.../ratesheet/*.csv` |

- **local mode** runs the kernel pipeline for a working union and asserts cell
  accuracy ≥ the Spec/09 §4.1 floor (704 ≥ 99.0%, 537 ≥ 67%).
- **deployed mode** uploads the union's Rate Notice through the API presigned-URL
  flow, which triggers the Step Functions pipeline (S3 → EventBridge).

Add union-specific fixtures here only if a test needs a document that is not part
of the kernel sample set.
