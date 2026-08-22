# Security

Do not report vulnerabilities in public issues while the project is private. Share
the smallest reproducible description with the repository owner through a private
channel.

Never commit API keys, tokens, voice references, face images, private memories, model
weights, or OS keychain exports. Plugins and model workers are untrusted boundaries;
their manifests are declarations, not sandboxes. Side effects require permission,
confirmation, and audit paths before release.
