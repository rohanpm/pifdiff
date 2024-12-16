import importlib
import inspect
import logging
from random import shuffle
from typing import Optional, Any, Dict
import types
from importlib.metadata import files as dist_files, version as dist_version
import os.path
import pkg_resources

from .. import schema
from .ast_enrich import AstEnricher


LOG = logging.getLogger("pidiff.dump")


class Dumper:
    def __init__(self, root_name, module):
        self.module = module
        self.root_name = root_name
        self.raw = {"root": {}, "objects": {}}
        self.object_refs = []

    @property
    def module_dir(self):
        return os.path.dirname(self.module.__file__)

    def run(self):
        self.raw["root"]["name"] = self.root_name

        version = get_version(self.root_name, self.module)
        if version:
            self.raw["root"]["version"] = version

        self.dump_object(
            ref=self.raw["root"], name=self.raw["root"]["name"], ob=self.module
        )

        enrich = AstEnricher()
        enrich.run(self.raw)

    def dump_object(self, ref, name, ob):
        ref_str = str(id(ob))
        ref["name"] = name
        ref["ref"] = ref_str

        if ref_str in self.raw["objects"]:
            # object itself is already known, nothing to store
            return

        # OK, object needs to be filled in.

        # We hold a reference to the object just to ensure it stays alive
        # for entire duration of the dump (otherwise cases could
        # arise where id() is reused)
        self.object_refs.append(ob)

        ob_data = {}
        self.raw["objects"][ref_str] = ob_data

        ob_data["object_type"] = get_object_type(ob)
        ob_data["is_callable"] = callable(ob)

        set_location(ob_data, ob)

        if not ob_data.get("file") or not ob_data.get("file").startswith(
            self.module_dir + "/"
        ):
            ob_data["is_external"] = True
            return

        ob_data["is_external"] = False

        if ob_data["is_callable"]:
            dump_signature(ob_data.setdefault("signature", []), ob)

        child_names = [attr for attr in dir(ob) if is_public(attr)]

        # The idea here is to improve robustness:
        # it seems like dir() returns attrs in sorted order.
        # That could lead to fragile dump/diff code which only happens to work
        # if children are always processed in the same order.
        # Let's randomize to ensure we can't write code relying on the order.
        shuffle(child_names)

        for child_name in child_names:
            try:
                child = getattr(ob, child_name)
            except Exception:
                LOG.debug("Can't getattr %s %s", ob, child_name, exc_info=True)
                continue

            child_ref = {}
            ob_data.setdefault("children", []).append(child_ref)

            LOG.debug("Descending to %s.%s %s", name, child_name, id(child))
            self.dump_object(ref=child_ref, name=child_name, ob=child)


def is_public(name) -> bool:
    # Note: don't think we need to include e.g. __init__ here,
    # because classes themselves fall into callable() case
    # and inspect.signature gives the signature of init
    return not name.startswith("_")


def get_file(value) -> Optional[str]:
    try:
        return inspect.getsourcefile(value)
    except TypeError:
        pass
    try:
        return value.__file__
    except Exception:
        pass
    try:
        module = importlib.import_module(value.__module__)
        return module.__file__
    except Exception:
        pass
    return None


def dump_signature(out, subject) -> None:
    try:
        sig = inspect.signature(subject)
    except ValueError:
        LOG.debug("Can't get signature for %s", subject, exc_info=True)
        return

    for param in sig.parameters.values():
        elem: Dict[str, Any] = {}
        elem["name"] = param.name
        elem["has_default"] = param.default is not param.empty
        elem["kind"] = str(param.kind)
        out.append(elem)


def get_object_type(value) -> str:
    class Klass:
        pass

    type_to_string = [
        (type(Klass), "class"),
        ((types.FunctionType, types.BuiltinFunctionType), "function"),
        ((types.MethodType, types.BuiltinMethodType), "method"),
        (types.ModuleType, "module"),
    ]

    for (type_instance, name) in type_to_string:
        if isinstance(value, type_instance):  # type: ignore
            return name

    return "object"


def set_location(out, subject) -> None:
    subject_file = get_file(subject)

    if subject_file:
        out["file"] = subject_file
        try:
            (_, lineno) = inspect.getsourcelines(subject)
            if lineno is not None:
                out["lineno"] = lineno
        except (OSError, TypeError):
            pass


def import_recurse(module_name: str):
    module = importlib.import_module(module_name)

    module_file = getattr(module, "__file__", None) or ""
    if os.path.basename(module_file) == "__init__.py":
        module_dir = os.path.dirname(module_file)
        for filename in os.listdir(module_dir):
            if not filename.startswith("_"):
                basename = filename.split(".")[0]
                try:
                    import_recurse(".".join([module_name, basename]))
                except (PermissionError, ModuleNotFoundError):
                    pass

    return module


def egg_for_root(root_name: str):
    # FIXME: why are both mypy and pylint getting things wrong here
    # for pkg_resources? Something wrong with the setup?
    for dist in pkg_resources.working_set:  # pylint: disable=not-an-iterable
        egg_info = dist.egg_info  # type: ignore
        if not egg_info:
            continue
        try:
            with open(os.path.join(egg_info, "top_level.txt")) as f:
                lines = [line.strip() for line in f.readlines()]
                if root_name in lines:
                    return dist
        except OSError:
            LOG.debug("Can't check %s", egg_info, exc_info=True)


def get_version_importlib(module) -> Optional[str]:
    module_file = module.__file__
    if module_file.endswith(".pyc"):
        module_file = module_file[:-1]

    for dist in pkg_resources.working_set:  # pylint: disable=not-an-iterable
        name = dist.project_name
        for file in dist_files(name) or []:
            if str(file.locate()) == module_file:
                return dist_version(name)

    return None


def get_version(root_name: str, module) -> Optional[str]:
    # importlib.metadata (best)
    from_importlib = get_version_importlib(module)
    if from_importlib:
        return from_importlib

    # PEP 396 (rejected)
    from_module = getattr(module, "__version__", None)
    if from_module:
        return from_module

    # OK then, try to find a relevant egg
    egg = egg_for_root(root_name)
    if egg:
        return egg.version

    return None


def dump_module(root_name: str) -> dict:
    """Dump a module.

    Arguments:

        root_name:
            Fully qualified name of the module serving as root of a public API,
            e.g. ``mypackage.mymodule``.

            This module must be importable (and will be imported) by the current
            Python interpreter.

    Returns:
        dump:
            Information on the module's public API.

            The specific data structure returned is undefined, but is valid for
            the following uses:

            - serialization and deserialization by JSON or pickling
            - as an argument to :func:`~pidiff.diff`
    """

    module = import_recurse(root_name)

    dumper = Dumper(root_name, module)
    dumper.run()

    out = dumper.raw
    schema.validate(out)

    return out
