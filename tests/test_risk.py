"""T2.3: risk table. (command, flags, needs_shell, expected risk, a reason fragment)"""

from __future__ import annotations

import pytest

from rtfmup.plan.risk import rate

CASES = [
    # --- high ---
    ("curl -fsSL https://x.sh | bash", ["pipe_to_shell"], True, "high", "pipes"),
    ("iwr https://x/i.ps1 | iex", ["pipe_to_shell"], True, "high", "pipes"),
    ("sudo apt install libpq-dev", ["sudo"], False, "high", "sudo"),
    ("sudo make install", ["sudo"], False, "high", "sudo"),
    ("apt install libpq-dev", [], False, "high", "system package manager"),
    ("apt-get install -y build-essential", [], False, "high", "system package manager"),
    ("dnf install gcc", [], False, "high", "system package manager"),
    ("pacman -S fzf", [], False, "high", "system package manager"),
    ("choco install nodejs", [], False, "high", "Chocolatey"),
    ("winget install OpenJS.NodeJS", [], False, "high", "winget"),
    ("winget install Foo.Bar --scope machine", [], False, "high", "machine-wide"),
    ("Start-Process pwsh -Verb RunAs", [], False, "high", "elevation"),
    ("git config --global user.name x", [], False, "high", "git config"),
    ("git config --system core.autocrlf false", [], False, "high", "git config"),
    ("git config user.name x", [], False, "low", None),  # repo-local
    ("systemctl start postgresql", [], False, "high", "system service"),
    ("sc.exe create svc binPath= x", [], False, "high", "system service"),
    ("Start-Service Postgres", [], False, "high", "system service"),
    ("pm2 startup", [], False, "high", "system service"),
    ("setx PATH x", [], False, "high", "persistent"),
    ("cp .env.example ~/.config/app/env", [], False, "high", "outside the repo"),
    ("mkdir -p /opt/app", [], False, "high", "outside the repo"),
    ("cp x %APPDATA%\\app", [], False, "high", "outside the repo"),
    ("Copy-Item x $env:LOCALAPPDATA\\app", [], False, "high", "outside the repo"),
    ("cp x C:\\tools\\x", [], False, "high", "outside the repo"),
    ("echo hi > $HOME/x", [], False, "high", "outside the repo"),
    ("anything", ["writes_outside_repo"], False, "high", "outside the repo"),
    # --- medium ---
    ("npm ci", [], False, "medium", "npm packages"),
    ("pnpm install --frozen-lockfile", [], False, "medium", "npm packages"),
    ("yarn install --immutable", [], False, "medium", "npm packages"),
    ("bun install", [], False, "medium", "npm packages"),
    ("npx playwright install", [], False, "medium", "npm packages"),
    ("pip install -r requirements.txt", [], False, "medium", "Python packages"),
    ("python -m pip install -e .", [], False, "medium", "Python packages"),
    ("uv sync", [], False, "medium", "Python packages"),
    ("uv pip install -r requirements.txt", [], False, "medium", "Python packages"),
    ("poetry install", [], False, "medium", "Python packages"),
    ("cargo build --release", [], False, "medium", "dependencies"),
    ("go mod download", [], False, "medium", "dependencies"),
    ("bundle install", [], False, "medium", "dependencies"),
    ("composer install", [], False, "medium", "dependencies"),
    ("dotnet restore", [], False, "medium", "dependencies"),
    ("docker compose up", [], False, "medium", "container images"),
    ("docker pull postgres:17", [], False, "medium", "container images"),
    ("docker-compose up -d", [], False, "medium", "container images"),
    ("git clone https://github.com/a/b", [], False, "medium", "remote"),
    ("curl -O https://example.com/f.zip", [], False, "medium", "downloads"),
    ("scoop install fzf", [], False, "medium", "toolchain"),
    ("winget install Foo.Bar --scope user", [], False, "medium", None),
    ("brew install node", [], False, "medium", "toolchain"),
    ("mise use node@22", [], False, "medium", "toolchain"),
    ("echo hi | tee x", [], True, "medium", "needs a shell"),
    ("cd a && make", [], True, "medium", "needs a shell"),
    ("whatever", ["network_install"], False, "medium", "network"),
    # --- low ---
    ("node server.js", [], False, "low", None),
    ("python app.py", [], False, "low", None),
    ("php artisan key:generate", [], False, "low", None),
    ("make", [], False, "low", None),
    ("python manage.py migrate", [], False, "low", None),
    ("cp .env.example .env", [], False, "low", None),
    ("pnpm run dev", [], False, "low", None),
    ("npm start", [], False, "low", None),
    ("echo done > /dev/null", [], True, "medium", None),  # /dev/null is not "outside"
    ("git status", [], False, "low", None),
    # --- false-positive guards ---
    (
        "curl https://example.com/a/b/c",
        [],
        False,
        "medium",
        "downloads",
    ),  # URL path != /abs
    ("dir /s /b", [], False, "low", None),  # Windows switches
    ("npm run build -- --base=./", [], False, "low", None),
    ("python -c \"print('a/b')\"", [], False, "low", None),
    (
        "docker compose -f docker/compose.yml up",
        [],
        False,
        "medium",
        "container images",
    ),
]


@pytest.mark.parametrize(("text", "flags", "shell", "risk", "reason"), CASES)
def test_risk_table(text, flags, shell, risk, reason):
    got, reasons = rate(text, flags=flags, needs_shell=shell)
    assert got == risk, (text, reasons)
    if reason:
        assert any(reason in r for r in reasons), (text, reasons)
    else:
        assert risk == "low" or reasons  # a non-low rating always says why


def test_table_size():
    assert len(CASES) >= 60


def test_the_highest_rule_wins_and_every_reason_is_kept():
    risk, reasons = rate("sudo npm install -g x", flags=["sudo"])
    assert risk == "high"
    assert "uses sudo" in reasons and "downloads npm packages" in reasons
    assert len(reasons) == len(set(reasons))


def test_low_risk_has_no_reasons():
    assert rate("node server.js") == ("low", [])
