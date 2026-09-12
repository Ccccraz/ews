# Changelog

## [0.2.0](https://github.com/Ccccraz/ews-cli/compare/v0.1.1...v0.2.0) (2026-09-12)


### ⚠ BREAKING CHANGES

* :recycle: rename project to ews-cli ([#24](https://github.com/Ccccraz/ews-cli/issues/24))

### Code Refactoring

* :recycle: rename project to ews-cli ([#24](https://github.com/Ccccraz/ews-cli/issues/24)) ([a085444](https://github.com/Ccccraz/ews-cli/commit/a0854443e5c6f223fc0ae03b9052b3cdbafe0390))

## [0.1.1](https://github.com/Ccccraz/ews/compare/v0.1.0...v0.1.1) (2026-09-12)


### Features

* **attachment:** :sparkles: add streaming attachment save command ([#6](https://github.com/Ccccraz/ews/issues/6)) ([786a85b](https://github.com/Ccccraz/ews/commit/786a85b35a96253f8c7dbc26decb085723bbd773))
* **auth:** :sparkles: add password deletion command ([#3](https://github.com/Ccccraz/ews/issues/3)) ([c811a42](https://github.com/Ccccraz/ews/commit/c811a42ca2492f3e9872547552508cc81fa6c9ce))
* **cli:** :sparkles: add config and auth commands ([#2](https://github.com/Ccccraz/ews/issues/2)) ([225e6a7](https://github.com/Ccccraz/ews/commit/225e6a7abff4d93d0e37f802b922ca7f3626939e))
* **cli:** ✨ add EWS profile configuration and access testing ([51c2680](https://github.com/Ccccraz/ews/commit/51c26807d84981625cfd00a81d40514c4b76a3d7))
* **config:** :sparkles: add multi-user profiles ([#16](https://github.com/Ccccraz/ews/issues/16)) ([55549be](https://github.com/Ccccraz/ews/commit/55549befc9eed828f7c203faf2523aca28912214))
* **contact:** :sparkles: cache personal contacts and search the directory ([#18](https://github.com/Ccccraz/ews/issues/18)) ([fdd07e5](https://github.com/Ccccraz/ews/commit/fdd07e5586a3c3e5065693956d8ca04c999eec35))
* **doctor:** :sparkles: add configuration, TLS and login diagnosis command ([#4](https://github.com/Ccccraz/ews/issues/4)) ([3b9c376](https://github.com/Ccccraz/ews/commit/3b9c376191caa855c68df76225158ca0a4aefdd9))
* **keyring:** :sparkles: classify backend failures and document cross-platform support ([#20](https://github.com/Ccccraz/ews/issues/20)) ([589b2be](https://github.com/Ccccraz/ews/commit/589b2be51d8416d9057288fd5fb21b352c61ab43))
* **logging:** :sparkles: add a loguru stderr diagnostics entry point ([#8](https://github.com/Ccccraz/ews/issues/8)) ([bb7a995](https://github.com/Ccccraz/ews/commit/bb7a995226a00a7d36f8f3b4eb033eca4c386fdd))
* **logging:** :sparkles: swap loguru for structlog json diagnostics ([#9](https://github.com/Ccccraz/ews/issues/9)) ([ba95148](https://github.com/Ccccraz/ews/commit/ba951481b3c5e58f64c74752736359e129c781ec))
* **mailbox:** :sparkles: add EWS read commands ([f4b42ba](https://github.com/Ccccraz/ews/commit/f4b42ba1198d107a81a7316175ff9e9fe25d9220))
* **message:** :sparkles: add draft creation commands ([#17](https://github.com/Ccccraz/ews/issues/17)) ([3696483](https://github.com/Ccccraz/ews/commit/36964833ee15a74cf6c55dc89044c02957cd8059))
* **message:** :sparkles: add message send, reply, mark-read and move commands ([#5](https://github.com/Ccccraz/ews/issues/5)) ([4ae5064](https://github.com/Ccccraz/ews/commit/4ae5064869d3926ea6a88c7c8d01efdfff405346))
* **storage:** :sparkles: add SQLModel mailbox cache ([1f5e4ab](https://github.com/Ccccraz/ews/commit/1f5e4abfc53247a893326944db6276b5cde5bb11))
* **sync:** ✨ add local-first mailbox synchronization ([#1](https://github.com/Ccccraz/ews/issues/1)) ([b379b0e](https://github.com/Ccccraz/ews/commit/b379b0e326f10c6a1f59108b5d201f0de5339943))
* **thread:** :sparkles: read a full exchange conversation thread ([#7](https://github.com/Ccccraz/ews/issues/7)) ([74e1ee3](https://github.com/Ccccraz/ews/commit/74e1ee30fc511a47c3abd7cc402cf924479394e1))


### Bug Fixes

* **cli:** :bug: report unexpected errors as the internal error envelope ([#12](https://github.com/Ccccraz/ews/issues/12)) ([869e9aa](https://github.com/Ccccraz/ews/commit/869e9aa507fa3ceba45df9828c250fc0e859aa5e))
* **folder:** :bug: resolve well-known folder names by distinguished id ([#11](https://github.com/Ccccraz/ews/issues/11)) ([1d19ced](https://github.com/Ccccraz/ews/commit/1d19ced9a60fc9b3559e51978c5da5b869dd0659))
* **message:** :bug: report the server change key after mark-read ([#10](https://github.com/Ccccraz/ews/issues/10)) ([34badce](https://github.com/Ccccraz/ews/commit/34badcef446cfd0ab89655db9539e76ab8fc83da))


### Documentation

* :memo: add bilingual readme covering usage and the output contract ([#13](https://github.com/Ccccraz/ews/issues/13)) ([1746928](https://github.com/Ccccraz/ews/commit/1746928c1f5a6c347262dbd41503fc2bf9271350))
* :memo: add MIT license ([b2db3cc](https://github.com/Ccccraz/ews/commit/b2db3ccbc4e23eea8a4b200936d01663785eee4d))
* :memo: add the implementation reference and the agent conventions ([#15](https://github.com/Ccccraz/ews/issues/15)) ([5dedd6a](https://github.com/Ccccraz/ews/commit/5dedd6af5b12ce126edfe4c910386976ea746090))
* :memo: require agents to write only through the draft series ([#19](https://github.com/Ccccraz/ews/issues/19)) ([f6c5118](https://github.com/Ccccraz/ews/commit/f6c5118a0ed20fc2f66e65a06555bc28ea38019b))
