import copy
import inspect
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Type, get_type_hints

import typing_inspect  # type: ignore
from dataclasses_jsonschema import JsonSchemaMixin
from typed_ast.ast3 import AST

from arcor2.data.common import ActionMetadata
from arcor2.data.object_type import ParameterMeta
from arcor2.docstring import parse_docstring
from arcor2.exceptions import Arcor2Exception
from arcor2.object_types.abstract import Generic, GenericWithPose
from arcor2.object_types.utils import built_in_types, get_settings_def, iterate_over_actions
from arcor2.parameter_plugins import TYPE_TO_PLUGIN
from arcor2.parameter_plugins.base import ParameterPlugin, ParameterPluginException
from arcor2.source.utils import SourceException, find_function, parse_def
from arcor2_arserver_data.objects import ObjectAction, ObjectTypeMeta
from arcor2_arserver_data.robot import RobotMeta


class ObjectTypeException(Arcor2Exception):
    pass


@dataclass
class ObjectTypeData:

    meta: ObjectTypeMeta
    type_def: Optional[Type[Generic]] = None
    actions: Dict[str, ObjectAction] = field(default_factory=dict)
    ast: Optional[AST] = None
    robot_meta: Optional[RobotMeta] = None

    def __post_init__(self):
        if not self.meta.disabled:
            assert self.type_def is not None
            assert self.ast is not None


ObjectTypeDict = Dict[str, ObjectTypeData]


def obj_description_from_base(data: ObjectTypeDict, obj_type: ObjectTypeMeta) -> str:

    try:
        obj = data[obj_type.base]
    except KeyError:
        raise Arcor2Exception(f"Unknown object type: {obj_type}.")

    if obj.meta.description:
        return obj.meta.description

    if not obj.meta.base:
        return ""

    return obj_description_from_base(data, data[obj.meta.base].meta)


def get_dataclass_params(type_def: Type[JsonSchemaMixin]) -> List[ParameterMeta]:
    """Analyzes properties of dataclass and returns their metadata.

    :param type_def:
    :return:
    """

    ret: List[ParameterMeta] = []

    sig = inspect.signature(type_def.__init__)

    # TODO Will this work for inherited properties? Make a test! There is also dataclasses.fields maybe...
    for name, ttype in get_type_hints(type_def.__init__).items():
        if name == "return":
            continue

        if issubclass(ttype, JsonSchemaMixin):
            pm = ParameterMeta(name=name, type="dataclass")  # TODO come-up with plugin for this?
            pm.children = get_dataclass_params(ttype)
        else:
            param_type = _resolve_param(name, ttype)
            assert param_type is not None
            pm = ParameterMeta(name=name, type=param_type.type_name())

            def_val = sig.parameters[name].default
            if def_val is not inspect.Parameter.empty:
                pm.default_value = param_type.value_to_json(def_val)

            # TODO description, ranges, etc.

        ret.append(pm)

    return ret


def meta_from_def(type_def: Type[Generic], built_in: bool = False) -> ObjectTypeMeta:

    obj = ObjectTypeMeta(
        type_def.__name__,
        type_def.description(),
        built_in=built_in,
        abstract=type_def.abstract(),
        has_pose=issubclass(type_def, GenericWithPose),
    )

    for base in inspect.getmro(type_def)[1:-1]:  # skip type itself (first base) and the last one (object)
        if issubclass(base, Generic):
            obj.base = base.__name__
            break

    try:
        obj.settings = get_dataclass_params(get_settings_def(type_def))
    except Arcor2Exception:
        pass

    return obj


def built_in_types_data() -> ObjectTypeDict:

    ret: ObjectTypeDict = {}

    # built-in object types / services
    for _, type_def in built_in_types():

        assert issubclass(type_def, Generic)

        ast = parse_def(type_def)

        d = ObjectTypeData(meta_from_def(type_def, built_in=True), type_def, object_actions(type_def, ast), ast)

        ret[d.meta.type] = d

    return ret


class IgnoreActionException(Arcor2Exception):
    pass


def _resolve_param(name: str, ttype) -> Type[ParameterPlugin]:

    try:
        return TYPE_TO_PLUGIN[ttype]
    except KeyError:
        for k, v in TYPE_TO_PLUGIN.items():
            if not v.EXACT_TYPE and inspect.isclass(ttype) and issubclass(ttype, k):
                return v

    # ignore action with unknown parameter type
    raise IgnoreActionException(f"Parameter {name} has unknown type {ttype}.")


def object_actions(type_def: Type[Generic], tree: AST) -> Dict[str, ObjectAction]:

    ret: Dict[str, ObjectAction] = {}

    # ...inspect.ismethod does not work on un-initialized classes
    for method_name, method_def in iterate_over_actions(type_def):

        meta: ActionMetadata = method_def.__action__  # type: ignore

        data = ObjectAction(name=method_name, meta=meta)

        if method_name in type_def.CANCEL_MAPPING:
            meta.cancellable = True

        try:

            if not method_def.__doc__:
                doc = {}
            else:
                doc = parse_docstring(method_def.__doc__)
                doc_short = doc["short_description"]
                if doc_short:
                    data.description = doc_short

            signature = inspect.signature(method_def)

            try:
                method_tree = find_function(method_name, tree)
            except SourceException:
                # function is probably defined in predecessor, will be added later
                continue

            for name, ttype in get_type_hints(method_def).items():

                if name == "return":

                    # ...just ignore NoneType for returns
                    if ttype == type(None):  # noqa: E721
                        continue

                    if typing_inspect.is_tuple_type(ttype):
                        for arg in typing_inspect.get_args(ttype):
                            resolved_param = _resolve_param(name, arg)
                            if resolved_param is None:
                                raise IgnoreActionException("None in return tuple is not supported.")
                            data.returns.append(resolved_param.type_name())
                    else:
                        # TODO resolving needed for e.g. enums - add possible values to action metadata somewhere?
                        data.returns = [_resolve_param(name, ttype).type_name()]

                    continue

                param_type = _resolve_param(name, ttype)

                assert param_type is not None

                args = ParameterMeta(name=name, type=param_type.type_name())
                try:
                    param_type.meta(args, method_def, method_tree)
                except ParameterPluginException as e:
                    raise IgnoreActionException(e) from e

                if name in type_def.DYNAMIC_PARAMS:
                    args.dynamic_value = True
                    dvp = type_def.DYNAMIC_PARAMS[name][1]
                    if dvp:
                        args.dynamic_value_parents = dvp

                def_val = signature.parameters[name].default
                if def_val is not inspect.Parameter.empty:
                    args.default_value = param_type.value_to_json(def_val)

                try:
                    args.description = doc["params"][name].strip()
                except KeyError:
                    pass

                data.parameters.append(args)

        except Arcor2Exception as e:
            data.disabled = True
            data.problem = e.message
            # TODO log exception

        ret[data.name] = data

    return ret


def add_ancestor_actions(obj_type_name: str, object_types: ObjectTypeDict) -> None:

    base_name = object_types[obj_type_name].meta.base

    if not base_name:
        return

    if object_types[base_name].meta.base:
        add_ancestor_actions(base_name, object_types)

    # do not add action from base if it is overridden in child
    # TODO rewrite (use adv. of dict!)
    for base_action in object_types[base_name].actions.values():
        for obj_action in object_types[obj_type_name].actions.values():
            if base_action.name == obj_action.name:

                # built-in object has no "origins" yet
                if not obj_action.origins:
                    obj_action.origins = base_name
                break
        else:
            action = copy.deepcopy(base_action)
            if not action.origins:
                action.origins = base_name
            object_types[obj_type_name].actions[action.name] = action


__all__ = [
    ObjectTypeException.__name__,
    ObjectTypeData.__name__,
    "ObjectTypeDict",
    meta_from_def.__name__,
    built_in_types_data.__name__,
    object_actions.__name__,
    add_ancestor_actions.__name__,
]