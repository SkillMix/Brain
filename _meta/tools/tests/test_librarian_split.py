"""librarian.py: a desk subject another registry holds is split on the desk."""
import librarian
import registry
from conftest import NOW, make_space
from test_librarian import HARBOR, errors


def test_a_desk_subject_the_brain_holds_is_split_on_the_desk(tmp_path):
    from conftest import mount_brain
    brain = make_space(tmp_path / "brain", profile="brain", name="brain", subjects=[dict(HARBOR, home="harbor")])
    root = make_space(tmp_path / "ada")
    mount_brain(root, brain)
    for n in range(7):
        for word in ("migration", "ports"):
            (root / "work" / "projects" / "harbor" / f"{word}-{n}").mkdir(parents=True)
    librarian.run(root, now=NOW, scorer=lambda r: (0, 0))
    (key,) = librarian.proposals(root)
    assert librarian.proposals(root)[key]["change"]["kind"] == "split"
    assert librarian.apply(root, key, "second agent", now=NOW, scorer=lambda r: (0, 0)) == "applied"
    reg = registry.Registry(root)
    assert reg.get("harbor-migration").part_of == "harbor", "the parts live in the desk's own registry"
    assert (root / "work/projects/harbor-migration/migration-3").is_dir()
    assert registry.Registry(brain).get("harbor-migration") is None, "the brain's registry is not touched"
    assert errors(root) == []
