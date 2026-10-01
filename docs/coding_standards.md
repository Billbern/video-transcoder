### 📄 DOCUMENT 10: Coding Standards

**Goal**: Eliminate subjective debates during code reviews. Enforce consistency via tooling.

*   **Python (Backend/Worker)**:
    *   *Formatter*: `Black` (line length 88).
    *   *Linter*: `Ruff` (replaces flake8/isort, extremely fast).
    *   *Type Hinting*: Strict. All function signatures must have type hints. Enforced via `mypy`.
*   **JavaScript/React (Frontend)**:
    *   *Formatter*: `Prettier`.
    *   *Linter*: `ESLint` (Airbnb config or standard).
    *   *Language*: TypeScript (Strongly recommended for a portfolio piece to show type safety).
*   **Workflow**: 
    *   Implement `pre-commit` hooks so code cannot be committed if it fails linting/formatting.
