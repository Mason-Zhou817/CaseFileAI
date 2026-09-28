# Third-Party Notices

This notice describes the **source-only** CaseFile AI v1.0.0 repository. It does not describe or authorize redistribution of a Windows ZIP, installed Python environment, downloaded model cache, or other binary bundle. The repository's own source is offered under [GNU AGPL v3](LICENSE). Third-party materials retain their own rights and terms.

## Distribution scope

| Class | Material | In this source repository? |
| --- | --- | --- |
| A — bundled | Synthetic evaluation PDFs containing embedded SimHei font subsets; six application Demo PNGs; project source and license text | Yes |
| B — installed from upstream | Python runtime packages, including PyMuPDF, RapidOCR, and their transitive dependencies | No package wheels or installed libraries are committed |
| C — fetched during model preparation | `BAAI/bge-small-zh-v1.5` weights | No |
| D — development and test | Test and build tools, including ReportLab, pytest, Ruff, and Mypy | Their source code and wheels are not committed |

`uv.lock` identifies packages and distribution hashes; it is not a copy of those packages. Installing dependencies can create additional obligations under their upstream terms. A future binary release requires a separate audit of the exact redistributed wheels, model files, licenses, copyright notices, and corresponding-source requirements.

## Runtime dependencies obtained from upstream

### PyMuPDF / MuPDF

- **Version:** PyMuPDF 1.28.2 in `uv.lock`.
- **Usage:** PDF parsing, page rendering, and document processing.
- **Upstream license:** PyMuPDF and MuPDF are offered under open-source AGPL and commercial licenses. CaseFile AI follows the AGPL route for this source release; this is not a claim of automatic compliance for every use or later binary build.
- **Source:** [PyMuPDF license and copyright](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright); [PyMuPDF repository](https://github.com/pymupdf/PyMuPDF). The complete AGPL v3 text is included in this repository's `LICENSE`.
- **Redistribution here:** No PyMuPDF wheel or MuPDF binary is committed.

### RapidOCR

- **Version:** RapidOCR 3.9.2 in `uv.lock`.
- **Usage:** Local OCR of scanned PDF pages.
- **Package license:** The v3.9.2 project describes its engineering code as Apache-2.0 and separately identifies Baidu as the OCR model copyright holder. The Python package and its model files are installed from upstream; they are not in this source repository.
- **Source:** [RapidOCR v3.9.2 README, License section](https://github.com/RapidAI/RapidOCR/blob/v3.9.2/README.md#license); [RapidOCR repository](https://github.com/RapidAI/RapidOCR).

The locked RapidOCR wheel's default ONNX configuration identifies the following files. The hashes below match both its `default_models.yaml` and files in the **local, ignored** `.venv`, not files in the public source tree:

| Default model | SHA-256 | Upstream location declared by RapidOCR 3.9.2 |
| --- | --- | --- |
| `PP-OCRv6_det_small.onnx` | `090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f` | [RapidAI ModelScope, detection](https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/det/PP-OCRv6_det_small.onnx) |
| `PP-OCRv6_rec_small.onnx` | `6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884` | [RapidAI ModelScope, recognition](https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv6/rec/PP-OCRv6_rec_small.onnx) |
| `ch_ppocr_mobile_v2.0_cls_mobile.onnx` | `e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c` | [RapidAI ModelScope, classification](https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx) |

Model weights are obtained as part of the upstream dependency installation or preparation. **They are not redistributed in this source repository.** Their model-specific rights and required attribution must be checked against upstream terms before a binary distribution. The RapidOCR v3.9.2 wheel inspected here has no `MODEL_LICENSES.md`; a file by that name was not available at the checked v3.9.2 or current repository root. The package's Apache-2.0 label alone is not treated as proof of the terms for these three converted weights.

### BAAI/bge-small-zh-v1.5

- **Usage:** Local text embeddings for retrieval, downloaded when models are prepared.
- **Pinned model revision:** `4e17e244a0fb63bfb78fca8fcf95079fcc664f5c` in the application configuration.
- **Model license:** The [official BAAI model repository](https://huggingface.co/BAAI/bge-small-zh-v1.5/tree/main) marks the model MIT. The model weights are not in this source repository; users obtain them from upstream during preparation. The Python libraries that load the model are separate dependencies with their own terms.

## Bundled synthetic PDF assets

All 22 committed synthetic PDFs were inspected. Eighteen contain 19 embedded **SimHei** TrueType subsets, identified as `AAAAAA+SimHei` or `AAAAAB+SimHei`; the remaining four have no embedded SimHei. The subset name table states `© Beijing ZhongYi Electronics Co., 1995-2005, All rights reserved`. The Windows `simhei.ttf` used by the PDF generators and every extracted subset inspected have `OS/2.fsType = 0x0008` (editable embedding), without the no-subsetting flag. References to Helvetica in these PDFs have no embedded Helvetica font data. No standalone font file is included in this source repository.

[Microsoft identifies SimHei as a Windows-supplied font](https://learn.microsoft.com/en-us/typography/font-list/simhei). Its [OpenType specification](https://learn.microsoft.com/en-us/typography/opentype/spec/os2#fstype) defines `0x0008` as permitting editable document embedding and `0x0100` as the separate no-subsetting restriction. Microsoft's [font redistribution FAQ](https://learn.microsoft.com/en-us/typography/fonts/font-faq#document-embedding) permits distribution of documents with embedded Windows fonts when the authoring application follows those embedding restrictions. This permission concerns the font subsets **inside the PDFs**, not redistribution of SimHei as a standalone font or inclusion in an application. The synthetic PDFs are produced by project generators using ReportLab from the Windows font path; ReportLab is a development/test dependency and is not shipped here.

The six PNGs under `docs/assets/github/` are screenshots of this project's synthetic-data demonstration. Their PNG metadata contains no text/EXIF fields beyond optional image DPI.

## Other dependencies and future bundles

The full Python dependency set is recorded in `pyproject.toml` and `uv.lock`, including direct and transitive packages. Those package artifacts are installed from their respective upstream sources; this repository does not copy their wheels, library source, or binary assets. Users and redistributors must follow the license and notice terms attached to the exact versions they install or repackage. In particular, this source notice is **not** a complete notice bundle for a future Windows installer or ZIP.
