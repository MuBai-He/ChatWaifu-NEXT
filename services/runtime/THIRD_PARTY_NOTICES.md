# Third-party notices

Runtime uses `tiktoken` 0.12.0 and redistributes its pinned `cl100k_base` reference
vocabulary for offline input estimates. It is not a native Claude/Gemini tokenizer.

- Source: https://github.com/openai/tiktoken/tree/0.12.0
- Vocabulary: https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken
- SHA-256: `223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`
- Copyright (c) 2022 OpenAI, Shantanu Jain
- License: MIT; full text ships beside the vocabulary in
  `src/chatwaifu_runtime/providers/data/LICENSE.tiktoken`, including wheel and sidecar builds.

The native `weixin_ilink` adapter implements protocol behavior described by:

- `openclaw-weixin` v2.4.6, commit `cef0bfc390393f716903e16d50408118047f87e0`
- Source: <https://github.com/Tencent/openclaw-weixin>
- Copyright (C) 2026 Tencent. All rights reserved.
- License: MIT

The MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
