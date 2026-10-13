# Supertonic Flutter Example

> **Archived model download:** From the repository root, run
> `hf download supertone-oss-archive/supertonic-3 --revision aafc6e32416a594460b32413efc49d7fe4ce6d46 --local-dir assets`
> before using this example. See the [archive setup guide](../README.md#quick-start)
> for installation. Hosted demos mentioned in historical release notes are not part of this archive.


This example demonstrates how to use Supertonic 3 in a Flutter application using ONNX Runtime.

> **Note:** This project uses the `flutter_onnxruntime` package ([https://pub.dev/packages/flutter_onnxruntime](https://pub.dev/packages/flutter_onnxruntime)). At the moment, only the macOS platform has been tested. Although the flutter_onnxruntime package supports several other platforms, they have not been tested in this project yet and may require additional verification.


<details>
<summary>Historical release notes</summary>

These entries describe past releases. Hosted services and support offers mentioned
here are no longer provided by this archive. Follow the [archive setup guide](../README.md#quick-start).

**2026.04.29** - 🎉 **Supertonic 3** released with 31-language support, improved reading accuracy, and v2-compatible public ONNX assets. [Demo](https://huggingface.co/spaces/Supertone/supertonic-3) | [Models](https://huggingface.co/supertone-oss-archive/supertonic-3)

**2025.12.10** - Added [6 new voice styles](https://huggingface.co/supertone-oss-archive/supertonic/tree/c6ad29bec69c380356a0cc393c75dc84a6b37e71/voice_styles) (M3, M4, M5, F3, F4, F5). See [Voices](https://github.com/supertone-oss-archive/supertonic-py/blob/main/docs/voices.md) for details

**2025.12.08** - Optimized ONNX models via [OnnxSlim](https://github.com/inisis/OnnxSlim) now available on [Hugging Face Models](https://huggingface.co/supertone-oss-archive/supertonic)

**2025.11.23** - Added and tested macos support.

</details>

## Multilingual Support

Supertonic 3 supports 31 languages. Select the appropriate language from the dropdown; see the main README for the full code list.

## Requirements

- Flutter SDK version ^3.5.0

## Running the Demo

```bash
flutter clean
flutter pub get
flutter run -d macos
```
