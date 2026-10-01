### 📄 DOCUMENT 11: Definition of Done (DoD)

A feature is not "done" until it meets all the following criteria:

1.  **Code**: Written, formatted via linters, and type-checked.
2.  **Tests**: Unit and integration tests written and passing. Coverage > 80% for new code.
3.  **Review**: Peer-reviewed (or self-reviewed with a 24-hour cool-down) for architectural alignment.
4.  **Documentation**: API docs (Swagger/FastAPI auto-docs) updated. If it's a major feature, the `TECHNICAL.md` is updated.
5.  **Deployment**: Successfully deployed to the Staging environment via CI/CD.
6.  **Verification**: The E2E happy path passes in Staging.

