"""Install the product's versioned skill resources into each session workspace."""
from pathlib import Path
from shutil import copyfile


SKILL_ROOT = Path(__file__).parent / 'skills'


def install_skills(workspace):
    destination = Path(workspace) / 'skills'
    for source in SKILL_ROOT.rglob('*'):
        if not source.is_file() or source.suffix not in ('.md', '.py', '.json'):
            continue
        target = destination / source.relative_to(SKILL_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(source, target)
