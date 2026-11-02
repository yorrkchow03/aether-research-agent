# Local execution and private data

Aether is a single-user local research application. It binds to loopback, checks browser origins and requires a session token for writes. It is not designed to be exposed to the public Internet.

The research model has an authorized host terminal with a bounded runtime and a credential-free child environment. This is **not an operating-system sandbox**: model-generated code can access files permitted by your account. Use a dedicated account/container before extending access to untrusted users. Source text is untrusted data, never an instruction to execute.

Model/search requests go to the configured Ark Agent Plan host. Public data APIs do not receive the Ark key. Optional Jev requests go only to TypeSafe and use a separate credential. Private portfolio records are refused by the Jev adapter. Reading a private ledger with the analyst may put its summary in model context and local reports; exporting such a report includes the cited private snapshot.

Do not include credentials, private financial records or complete runtime logs in an issue. Reproduce problems using synthetic examples and share only redacted diagnostics. If a credential has been published, replace it through its provider before sharing more diagnostics.
