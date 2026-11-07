"""T1.9: classifier table. (command, headings, expected category, variant, platform, flags)"""

from __future__ import annotations

import pytest
from conftest import FIXTURES

from rtfmup.models import Command, SourceRef
from rtfmup.readme.classify import (
    category_for,
    classify,
    flags_for,
    needs_shell,
    platform_for,
    split_segments,
    variant_for,
)
from rtfmup.readme.extract import extract
from rtfmup.readme.parse import parse

REF = SourceRef(file="README.md", line_start=1, line_end=1, detector="readme.fence")


def mk(
    text: str, path: list[str] | None = None, shell: str = "bash", flags=None
) -> Command:
    return Command(
        id="cmd-1", text=text, shell=shell, category="other", reason="unclassified",
        section_path=path or [], flags=flags or [], confidence="medium", evidence=[REF],
    )  # fmt: skip


A = "any"
U = "unix"
# (text, headings, shell, category, variant, platform, flags)
CASES = [
    # --- install (prefix) ---
    ("npm install", [], "bash", "install", None, A, []),
    ("npm ci", ["Setup"], "bash", "install", None, A, []),
    ("pnpm install --frozen-lockfile", [], "bash", "install", None, A, []),
    ("yarn", [], "bash", "install", None, A, []),
    ("bun install", [], "bash", "install", None, A, []),
    ("pip install -r requirements.txt", [], "bash", "install", None, A, []),
    ("python -m pip install -e .", [], "bash", "install", None, A, []),
    ("uv sync", ["Installation"], "bash", "install", None, A, []),
    ("uv pip install flask", [], "bash", "install", None, A, []),
    ("poetry install", [], "bash", "install", None, A, []),
    ("cargo build --release", [], "bash", "install", None, A, []),
    ("go mod download", [], "bash", "install", None, A, []),
    ("bundle install", [], "bash", "install", None, A, []),
    ("composer install", [], "bash", "install", None, A, []),
    ("dotnet restore", [], "bash", "install", None, A, []),
    ("sudo apt install -y libpq-dev", [], "bash", "install", None, U, ["sudo"]),
    ("brew install node@20", ["macOS"], "bash", "install", "macos", "macos", []),
    ("winget install OpenJS.NodeJS.LTS", ["Windows"], "powershell", "install", "windows", "windows", []),
    ("choco install nodejs", [], "bash", "install", None, "windows", []),
    ("scoop install python", [], "bash", "install", None, "windows", []),
    ("git clone https://github.com/x/y.git", [], "bash", "install", None, A, []),
    # --- setup ---
    ("cp .env.example .env", [], "bash", "setup", None, A, []),
    ("php artisan key:generate", [], "bash", "setup", None, A, []),
    ("bin/rails db:setup", [], "bash", "setup", None, A, []),
    ("python manage.py migrate", [], "bash", "setup", None, A, []),
    ("pre-commit install", ["Contributing"], "bash", "setup", None, A, []),
    ("npm run setup", [], "bash", "setup", None, A, []),
    ("python3 -m venv venv", [], "bash", "setup", None, A, []),
    ("source venv/bin/activate", [], "bash", "setup", None, U, []),
    ("systemctl start postgresql", ["Linux"], "bash", "setup", "linux", "linux", ["sudo"][:0]),
    # --- run ---
    ("npm run dev", [], "bash", "run", None, A, []),
    ("npm start", [], "bash", "run", None, A, []),
    ("pnpm dev", [], "bash", "run", None, A, []),
    ("yarn start", [], "bash", "run", None, A, []),
    ("python app.py", ["Usage"], "bash", "run", None, A, []),
    ("uvicorn main:app --reload", [], "bash", "run", None, A, []),
    ("flask run", [], "bash", "run", None, A, []),
    ("cargo run", [], "bash", "run", None, A, []),
    ("go run .", [], "bash", "run", None, A, []),
    ("dotnet run --project Web.csproj", [], "bash", "run", None, A, []),
    ("docker compose up", [], "bash", "run", "docker", A, []),
    ("docker-compose up -d --build", ["Docker"], "bash", "run", "docker", A, []),
    ("bin/rails server", [], "bash", "run", None, A, []),
    ("php artisan serve", [], "bash", "run", None, A, []),
    ("uv run py-http", [], "bash", "run", None, A, []),
    # --- build ---
    ("npm run build", [], "bash", "build", None, A, []),
    ("make build", [], "bash", "build", None, A, []),
    ("make", ["Building"], "bash", "build", None, A, []),
    # --- test / lint ---
    ("pytest -x", ["Testing"], "bash", "test", None, A, []),
    ("uv run pytest", [], "bash", "test", None, A, []),
    ("npm test", [], "bash", "test", None, A, []),
    ("npm run test:unit", [], "bash", "test", None, A, []),
    ("cargo test", [], "bash", "test", None, A, []),
    ("go test ./...", [], "bash", "test", None, A, []),
    ("ruff check .", [], "bash", "lint", None, A, []),
    ("uv run ruff format .", [], "bash", "lint", None, A, []),
    ("npx eslint .", [], "bash", "lint", None, A, []),
    ("npm run lint", [], "bash", "lint", None, A, []),
    ("black .", [], "bash", "lint", None, A, []),
    # --- flags ---
    ("curl -fsSL https://astral.sh/uv/install.sh | sh", [], "bash", "install", None, U, ["pipe_to_shell"]),
    ("wget -qO- https://x.example/i.sh | sudo bash", [], "bash", "install", None, U, ["pipe_to_shell", "sudo"]),
    ("irm https://x.example/i.ps1 | iex", [], "powershell", "install", None, "windows", ["pipe_to_shell"]),
    ("iex (iwr https://x.example/i.ps1)", [], "powershell", "install", None, "windows", ["pipe_to_shell"]),
    ('bash <(curl -s https://x.example/i.sh)', [], "bash", "install", None, U, ["pipe_to_shell"]),
    ("sudo npm install -g pnpm", [], "bash", "install", None, U, ["sudo"]),
    # --- platform ---
    ("chmod +x ./run.sh", [], "bash", "setup", None, U, []),
    ("./scripts/bootstrap.sh", ["Setup"], "bash", "setup", None, U, []),
    ("export API_URL=http://localhost:3000", [], "bash", "setup", None, A, []),
    ("export -f myfunc", [], "bash", "setup", None, U, []),
    ("mkdir -p build", [], "bash", "setup", None, A, []),
    ("cd server", [], "bash", "setup", None, A, []),
    (".\\.venv\\Scripts\\Activate.ps1", ["Windows"], "powershell", "setup", "windows", "windows", []),
    ("npm install", [], "cmd", "install", None, "windows", []),
    ("Get-ChildItem", [], "bash", "other", None, "windows", []),
    # --- chains: navigation is not the point ---
    ("cd app && npm ci", [], "bash", "install", None, A, []),
    ("cd server && python main.py", [], "bash", "run", None, A, []),
    ("npm ci && npm start", [], "bash", "install", None, A, []),
    # --- heading context ---
    ("python scripts/init_db.py", ["Setup"], "bash", "setup", None, A, []),
    ("python scripts/init_db.py", ["Installation"], "bash", "setup", None, A, []),
    ("python scripts/seed.py", ["Usage"], "bash", "run", None, A, []),
    ("frobnicate --all", ["Installation"], "bash", "install", None, A, []),
    ("frobnicate --all", ["Running"], "bash", "run", None, A, []),
    ("frobnicate --all", ["Contributing"], "bash", "other", None, A, []),
    ("frobnicate --all", [], "bash", "other", None, A, []),
    ("make", ["Usage"], "bash", "run", None, A, []),
    # --- variants from headings ---
    ("npm install", ["Docker"], "bash", "install", "docker", A, []),
    ("npm install", ["Install", "From source"], "bash", "install", "native", A, []),
    ("npm install", ["Install", "Windows", "Details"], "bash", "install", "windows", A, []),
    ("npm install", ["Install", "Ubuntu/Debian"], "bash", "install", "linux", A, []),
    ("npm install", ["Local development"], "bash", "install", "native", A, []),
    ("npm install", ["Machine learning"], "bash", "install", None, A, []),  # "mac" inside a word
]  # fmt: skip


@pytest.mark.parametrize(
    ("text", "path", "shell", "category", "variant", "platform", "flags"),
    CASES,
    ids=[f"{i:02}-{c[0][:30]}" for i, c in enumerate(CASES)],
)
def test_classification_table(text, path, shell, category, variant, platform, flags):
    got = classify(mk(text, path, shell))
    assert got.category == category, got.reason
    assert got.variant == variant
    assert got.platform == platform
    assert sorted(got.flags) == sorted(flags)


def test_table_has_at_least_forty_cases():
    assert len(CASES) >= 40


def test_reason_records_the_rule():
    assert classify(mk("npm ci")).reason == "prefix:package install"
    assert classify(mk("cp .env.example .env")).reason == "prefix:cp .env"
    assert classify(mk("curl x | sh")).reason == "flag:pipe_to_shell"
    r = classify(mk("frobnicate", ["Installation"])).reason
    assert r.startswith("heading:install (Installation)") and "no prefix rule" in r
    r = classify(mk("python scripts/x.py", ["Setup"])).reason
    assert "overrides weak prefix:python script" in r
    assert classify(mk("frobnicate")).reason == "no rule matched"


def test_strong_prefix_beats_heading():
    # `npm install` under a Usage heading is still an install.
    assert classify(mk("npm install", ["Usage"])).category == "install"
    assert classify(mk("pytest", ["Installation"])).category == "test"


def test_deepest_heading_wins():
    assert classify(mk("frobnicate", ["Installation", "Running"])).category == "run"
    assert variant_for(["Docker", "Windows"]) == "windows"


# --- flags -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "curl -fsSL https://x.example/a.sh | sh",
        "curl https://x | bash",
        "wget -O- https://x | zsh",
        "iwr https://x | iex",
        "Invoke-WebRequest https://x | Invoke-Expression",
        "iex (irm https://x)",
        'sh -c "$(curl -fsSL https://x)"',
        "bash <(wget -qO- https://x)",
    ],
)
def test_pipe_to_shell_detected(text):
    assert "pipe_to_shell" in flags_for(text)


@pytest.mark.parametrize(
    "text",
    [
        "curl -o out.tgz https://x",
        "curl https://x | jq .",
        "wget https://x && sh install.sh",  # reviewed separately, not a pipe
        "echo curl | grep sh",
        "brew install curl",
        "cat a | bash-completion",
    ],
)
def test_pipe_to_shell_false_positives(text):
    assert "pipe_to_shell" not in flags_for(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sudo apt install x", True),
        ("curl x | sudo bash", True),
        ("cd a && sudo make install", True),
        ("echo sudoku", False),
        ("pseudo-code", False),
        ("npm install sudo-prompt", False),
    ],
)
def test_sudo_flag(text, expected):
    assert ("sudo" in flags_for(text)) is expected


def test_existing_flags_are_preserved():
    got = classify(mk("sudo make", flags=["placeholder"]))
    assert got.flags == ["placeholder", "sudo"]


# --- needs_shell (D17) -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "a | b",
        "a && b",
        "a || b",
        "a; b",
        "a > f",
        "a < f",
        "echo $(date)",
        "echo `date`",
        "PORT=3000 npm start",
        "a & b",
    ],
)
def test_needs_shell(text):
    assert needs_shell(text)
    assert classify(mk(text)).needs_shell


@pytest.mark.parametrize(
    "text",
    [
        'echo "a | b"',
        "echo 'a && b'",
        "npm install foo@^1.0",
        "uv run app --port 3000",
        'grep "x>y" f',
    ],
)
def test_needs_shell_false(text):
    assert not needs_shell(text)


def test_split_segments():
    assert split_segments('cd a && echo "x && y" | cat; ls || pwd') == [
        "cd a",
        'echo "x && y"',
        "cat",
        "ls",
        "pwd",
    ]


def test_platform_mixed_chain_is_unix():
    assert platform_for("sudo apt update && brew install x", "bash") == "unix"


def test_category_for_pipeline_uses_first_segment():
    assert category_for("npm ci | tee log", [], [])[0] == "install"


# --- end to end on fixtures ------------------------------------------------------


def classify_fixture(name: str):
    text = (FIXTURES / name / "README.md").read_text(encoding="utf-8")
    return [classify(c) for c in extract(parse(text), "README.md").commands]


def test_unix_only_fixture():
    got = [
        (c.text, c.category, c.platform, tuple(c.flags))
        for c in classify_fixture("unix-only")
    ]
    assert got == [
        ("sudo apt install python3-venv libpq-dev", "install", "unix", ("sudo",)),
        ("python3 -m venv venv", "setup", "any", ()),
        ("source venv/bin/activate", "setup", "unix", ()),
        ("pip install -r requirements.txt", "install", "any", ()),
        (
            "curl -fsSL https://example.com/install.sh | bash",
            "install",
            "unix",
            ("pipe_to_shell",),
        ),
        ("python app.py", "run", "any", ()),
    ]


def test_windows_fixture_is_windows_platform():
    cmds = classify_fixture("windows-readme")
    assert {c.platform for c in cmds} == {"windows"}
    assert [c.category for c in cmds][-1] == "run"


def test_docker_or_native_fixture_variants():
    got = [
        (c.text, c.category, c.variant) for c in classify_fixture("docker-or-native")
    ]
    assert got == [
        ("pip install -r requirements.txt", "install", "native"),
        ("python app.py", "run", "native"),
        ("docker compose up", "run", "docker"),
    ]


def test_macos_fixture_platforms():
    got = {c.text: (c.variant, c.platform) for c in classify_fixture("macos-readme")}
    assert got["xcode-select --install"] == ("macos", "macos")
    assert got["brew install node@20"] == ("macos", "macos")
    assert got["sudo apt install nodejs npm"] == ("linux", "unix")
    assert got["winget install OpenJS.NodeJS.LTS"][1] == "windows"


def test_linux_fixture():
    got = {c.text: c.category for c in classify_fixture("linux-readme")}
    assert got["sudo systemctl start postgresql"] == "setup"
    assert got["sudo dnf install postgresql-server"] == "install"
    assert got["sudo pacman -S postgresql"] == "install"


def test_every_fixture_command_gets_a_reason():
    for p in FIXTURES.iterdir():
        if (p / "README.md").exists():
            for c in classify_fixture(p.name):
                assert c.reason and c.reason != "unclassified"
