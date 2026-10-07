#!/bin/sh
# Experience rerun semantics, run INSIDE the booted live system.
#
# Delivered and executed the same way as kedra-live-checks.sh:
#
#   tests/kedra-iso-testvm ISO --script kedra-installer-rerun-checks.sh
#
# WHY THIS RUNS IN THE GUEST
# --------------------------
# The installer's Experience job may run more than once in one installer
# process: the page can be revisited, so a user may resolve Kedra, go back and
# choose Vanilla. What the installation then installs must depend only on the
# last answer.
#
# kedra-calamares' own suite proves that against the source tree, and against
# the built package. Neither of those is the live medium. This one drives the
# job module that the ISO actually carries, at /usr/lib/calamares/modules, with
# the resolver the ISO actually carries, at /usr/bin/kedra-experience, reading
# the manifests the ISO actually carries -- so what is asserted is the artifact
# a user boots rather than a checkout that resembles it.
#
# It is NOT a substitute for driving Calamares itself: no partitioning happens,
# no packages are installed, and nothing is written anywhere. What it exercises
# is exactly the GlobalStorage decision that `packages@online` later acts on,
# which is where the contamination lived.

exec 2>&1
echo "===== Experience rerun semantics, in the live system ====="

python3 - <<'PY'
import importlib.util
import json
import subprocess
import sys
import types

MODULE = "/usr/lib/calamares/modules/kedraexperiencepackages/main.py"
RESOLVER = "/usr/bin/kedra-experience"

failures = []


def check(ok, description, detail=""):
    print(("PASS  " if ok else "FAIL  ") + description)
    if detail:
        print("      " + str(detail))
    if not ok:
        failures.append(description)


# --- the libcalamares Calamares would provide -------------------------------
class GlobalStorage:
    def __init__(self):
        self.data = {}

    def contains(self, key):
        return key in self.data

    def value(self, key):
        return self.data.get(key)

    def insert(self, key, value):
        self.data[key] = value

    def remove(self, key):
        self.data.pop(key, None)


class Job:
    def __init__(self, configuration):
        self.configuration = configuration


libcalamares = types.ModuleType("libcalamares")
utils = types.ModuleType("libcalamares.utils")
utils.debug = lambda text: None
utils.warning = lambda text: None
utils.error = lambda text: None
utils.gettext_path = lambda: None
utils.gettext_languages = lambda: ["en"]
libcalamares.utils = utils
libcalamares.globalstorage = GlobalStorage()
libcalamares.job = Job({"resolver": RESOLVER})
sys.modules["libcalamares"] = libcalamares
sys.modules["libcalamares.utils"] = utils

spec = importlib.util.spec_from_file_location("kedraexperiencepackages", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(f"module:   {MODULE}")
print(f"resolver: {RESOLVER}")

OTHER = {"install": ["gdm", "gnome-shell"], "source": "netinstall@"}


def fresh():
    libcalamares.globalstorage.data = {"packageOperations": [dict(OTHER)]}
    libcalamares.job.configuration["resolver"] = RESOLVER


def resolve(selection):
    libcalamares.globalstorage.insert(
        "kedraExperienceSelection", json.dumps(selection)
    )
    return module.run()


def operations():
    return libcalamares.globalstorage.value("packageOperations") or []


def ours():
    return [
        op for op in operations()
        if op.get("source") == "kedraexperiencepackages"
    ]


def injected():
    return [name for op in ours() for name in op.get("install", [])]


def resolver_says(selection):
    done = subprocess.run(
        [RESOLVER, "resolve", "--selection", "-"],
        input=json.dumps(selection), capture_output=True, text=True, check=False,
    )
    return json.loads(done.stdout)["packages"]["required"]


KEDRA = {"base_preset": "kedra"}
VANILLA = {"base_preset": "vanilla"}
CUSTOM = {"base_preset": "kedra",
          "module_overrides": [{"id": "workflow", "enabled": False}]}

# --- A. Kedra -> Vanilla ----------------------------------------------------
fresh()
check(resolve(KEDRA) is None, "A: resolving Kedra succeeds")
check(len(ours()) == 1, "A: one Experience operation after Kedra", ours())
first = list(injected())
check(resolve(VANILLA) is None, "A: rerunning as Vanilla succeeds")
check(ours() == [], "A: the Kedra operation is GONE after the Vanilla rerun", ours())
leaked = sorted({n for op in operations() for n in op.get("install", [])
                 if n.startswith("kedra-")})
check(leaked == [], "A: no Kedra package would be installed", leaked)
check(operations() == [OTHER], "A: only other modules' operations remain", operations())
check(libcalamares.globalstorage.value("kedraExperienceAppliesLayer") is False,
      "A: the recorded layer state is exactly False")
check(json.loads(libcalamares.globalstorage.value(
    "kedraExperienceResolvedSelection"))["base_preset"] == "vanilla",
    "A: the recorded selection is the Vanilla one")

# --- B. Custom (non-empty) -> Vanilla ---------------------------------------
fresh()
resolve(CUSTOM)
check(len(ours()) == 1, "B: one Experience operation after Custom")
check(resolve(VANILLA) is None, "B: rerunning as Vanilla succeeds")
check(ours() == [], "B: the Custom operation is GONE after the Vanilla rerun", ours())

# --- C. Kedra -> smaller Custom ---------------------------------------------
fresh()
resolve(KEDRA)
resolve(CUSTOM)
check(len(ours()) == 1, "C: still exactly one Experience operation", ours())
check(injected() == resolver_says(CUSTOM),
      "C: the operation is the new profile's, not a union", injected())
check("kedra-experience-workflow" in first
      and "kedra-experience-workflow" not in injected(),
      "C: the package the new profile dropped is gone")

# --- D. Vanilla -> Kedra ----------------------------------------------------
fresh()
resolve(VANILLA)
check(ours() == [], "D: Vanilla adds no operation")
resolve(KEDRA)
check(len(ours()) == 1, "D: Kedra adds one operation")
check(injected() == resolver_says(KEDRA),
      "D: it is exactly what the resolver required", injected())
check(libcalamares.globalstorage.value("kedraExperienceAppliesLayer") is True,
      "D: the recorded layer state is exactly True")

# --- E. Kedra -> Kedra ------------------------------------------------------
fresh()
resolve(KEDRA)
before = json.dumps(operations(), sort_keys=True)
resolve(KEDRA)
check(len(ours()) == 1, "E: rerunning the same profile does not duplicate")
check(json.dumps(operations(), sort_keys=True) == before,
      "E: the operations are unchanged")

# --- F. A failed rerun ------------------------------------------------------
fresh()
resolve(KEDRA)
before = json.dumps(operations(), sort_keys=True)
libcalamares.job.configuration["resolver"] = "/nonexistent/kedra-experience"
result = resolve(VANILLA)
check(result is not None, "F: a failed rerun stops the installation", result)
check(json.dumps(operations(), sort_keys=True) == before,
      "F: it leaves the previously valid operation exactly as it was")

print()
if failures:
    print(f"RERUN CHECKS RESULT: FAIL ({len(failures)} check(s))")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("RERUN CHECKS RESULT: PASS")
PY
_rc=$?

echo
echo "===== installed Experience releases in the live system ====="
pacman -Q kedra-calamares kedra-experience kedra-experience-runtime

echo
echo "===== DONE ====="
exit "${_rc}"
