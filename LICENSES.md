# Licensing status

The ChatWaifu NEXT source code is licensed under the **PolyForm Noncommercial License 1.0.0**
(see `LICENSE`). Noncommercial use, personal study, local research, and noncommercial modification
are permitted. Commercial use, paid SaaS, productized commercial distribution, and proprietary
derivatives require a separate commercial license from the project owner.

Third-party code, models, voice assets, Live2D Core, Cubism models, and character
resources retain their own licenses. Every future worker/model manifest must include
a reviewed license identifier. Proprietary Live2D Core and unreviewed character
assets must not be committed.

The public Live2D Cubism Web Framework is fetched on demand from the official repository at the
pinned `5-r.5` release and remains in an ignored vendor checkout. It is governed by Live2D's own
SDK/framework terms. Cubism Core, MotionSync Core, sample models, and project character assets are
not redistributed by this repository; review their terms separately before local use or release.

The local Demo setup currently uses the Natori sample shipped with Cubism SDK for Web 5 R5. Its
Core, Demo source, generated bridge, textures, motions, and model files remain Git-ignored and are
used only for local validation. Natori retains the SDK's sample-model and Free Material License
terms; this repository does not grant redistribution or commercial-use rights for those files.

The owner-only Ayachi Nene Live2D overlay is adapted from Bilibili creator **涂抹一画**'s post
[“[Live2D模型免费分享] 拥有全服装的Live2D宁宁！”](https://www.bilibili.com/video/BV1MLgYzmEz9).
This attribution records the supplied model source; it does not by itself establish commercial-use,
secondary-distribution, character-IP, or bundled-installer rights. The adapted model remains
Git-ignored and private pending a release-specific license review.

The native WeChat iLink adapter implements HTTP and QR-login behavior from Tencent's published
`openclaw-weixin` v2.4.6 reference at commit
`cef0bfc390393f716903e16d50408118047f87e0`. That reference is Copyright (C) 2026 Tencent and
licensed under the MIT License. See `services/runtime/THIRD_PARTY_NOTICES.md`.

Frontend functional icons are rendered with `lucide-react`. Lucide is Copyright (c) 2026 Lucide
Icons and Contributors and licensed under the ISC License; portions derived from Feather are
Copyright (c) 2013-present Cole Bemis and licensed under the MIT License. The AI-drawn ChatWaifu
crescent-and-ribbon application mark is project artwork and is not part of Lucide. See
`docs/architecture/icon-system.md` for its canonical source and generated copies.

### Lucide ISC License

Permission to use, copy, modify, and/or distribute this software for any purpose with or without
fee is hereby granted, provided that the above copyright notice and this permission notice appear
in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS
SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE
AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT,
NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE
OF THIS SOFTWARE.

### Feather MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
