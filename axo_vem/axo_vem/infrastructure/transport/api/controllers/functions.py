from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from pymongo.collection import Collection
from axo_vem.infrastructure.auth.service_token import ActingUser

from axo_shared.functions.source import FunctionSourceError, validate_function_source
from axo_shared.functions.signature import check_source

from axo_vem.application.compute.default_version import FunctionDefaults
from axo_vem.application.compute.delete_function import DeleteFunctionUseCase
from axo_vem.application.compute.purge_function_version import PurgeFunctionVersionUseCase
from axo_vem.application.compute.register_function import CODE_FORMATS, RegisterFunctionUseCase
from axo_vem.application.compute.update_function import UpdateFunctionUseCase
from axo_vem.application.compute.function_secrets import masked
from axo_vem.domain.errors import NotFoundError, NotOwnerError
from axo_vem.infrastructure.transport.api.serialization import strip_id


class _CheckRequest(BaseModel):
    name: str
    source: str
    function_id: Optional[str] = None


class _DefaultVersionRequest(BaseModel):
    version: int


class _FunctionUpdateRequest(BaseModel):
    params_schema: Optional[List[Dict[str, Any]]] = None
    env_vars: Optional[Dict[str, Optional[str]]] = None    # a secret sent as None keeps its value
    # the Settings tab saves everything at once:
    params_schema_replace: bool = False
    env_vars_replace: bool = False
    secret_env: Optional[List[str]] = None
    runtime: Optional[Dict[str, Any]] = None              # max_attempts, retry_*, keep_result, idle_ttl_seconds, ...


def _shown(doc: Dict[str, Any], user: ActingUser) -> Dict[str, Any]:
    """A function version as this person may see it: env var values only for
    Developers and above, a secret's value never."""
    doc = strip_id(doc)
    if doc.get("runtime_spec"):
        doc["runtime_spec"] = masked(doc["runtime_spec"], show_values=user.at_least("developer"))
    doc.pop("env_vars", None)
    return doc


def _may_delete(doc: Dict[str, Any], user: ActingUser) -> None:
    """Admins delete any version; a Developer only versions they registered."""
    if user.at_least("admin") or (doc.get("registered_by") and doc.get("registered_by") == user.key):
        return
    raise NotOwnerError("only an Admin, or the Developer who registered this version, can delete it")


def build_router(
    functions: Collection,
    current_user_dependency: Callable[..., ActingUser],
    register_use_case: RegisterFunctionUseCase,
    delete_use_case: DeleteFunctionUseCase,
    update_use_case: UpdateFunctionUseCase,
    purge_use_case: PurgeFunctionVersionUseCase,
    code_store: Any = None,                 # FunctionCodeStore (the VE's code bucket)
    defaults: Optional[FunctionDefaults] = None,
) -> APIRouter:
    router = APIRouter(tags=["function registries"])

    def _with_default(docs: List[Dict[str, Any]], user: ActingUser) -> List[Dict[str, Any]]:
        """Each version, with ``is_default``: the one that runs when none is named."""
        chosen = defaults.of_many(d["function_id"] for d in docs) if defaults is not None else {}
        return [{**_shown(d, user), "is_default": chosen.get(d["function_id"]) == d.get("version")} for d in docs]

    @router.get("/functions")
    def list_functions(current_user: ActingUser = Depends(current_user_dependency)):
        return _with_default(list(functions.find()), current_user)

    @router.get("/functions/{function_id}")
    def get_function_versions(function_id: str, current_user: ActingUser = Depends(current_user_dependency)):
        """Every version of one function -- _id is f"{function_id}:{version}",
        so a prefix match on the function_id field (not _id) finds them all."""
        docs = list(functions.find({"function_id": function_id}))
        if not docs:
            raise HTTPException(status_code=404, detail="function not found")
        return _with_default(docs, current_user)

    @router.post("/functions/check")
    def check_function(body: _CheckRequest, _current_user: ActingUser = Depends(current_user_dependency)):
        """The browser's live check of code being written: problems (with
        their line), the run form it would get, its hash -- never runs it.
        ``existing_version``: the version of ``function_id`` with this same
        code (saving it again changes nothing)."""
        out = check_source(body.source.encode("utf-8"), body.name)
        out["existing_version"] = None
        if body.function_id:
            same = functions.find_one({"function_id": body.function_id, "code_hash": out["code_hash"],
                                       "deleted_at": None}, sort=[("version", -1)])
            out["existing_version"] = same["version"] if same else None
        return out

    @router.post("/functions", status_code=201)
    async def register_function(
        name: str = Form(...),
        runtime_spec: Optional[str] = Form(None),
        params_schema: Optional[str] = Form(None),
        code_format: str = Form("source"),
        code_hash: Optional[str] = Form(None),
        call_style: Optional[str] = Form(None),
        display_source: Optional[str] = Form(None),
        function_id: Optional[str] = Form(None),
        code: UploadFile = File(...),
        current_user: ActingUser = Depends(current_user_dependency),
    ):
        """Registers a function into a virtual environment the caller owns --
        no endpoint_id from the caller; RegisterFunctionUseCase auto-picks
        the target endpoint currently assigned to that VE.

        `code` is raw .py source -- must define a top-level function named
        exactly `name` with named parameters (e.g. name="add" requires
        `def add(a: int, b: int = 1, ctx=None): ...`; the old
        `def add(params, ctx)` is refused). This service only statically validates that shape via
        axo_shared.functions.source.validate_function_source (parses the
        AST, never executes it) and forwards the raw source onward
        untouched -- actual execution is deferred to the target node's own
        cold start (forked worker or spawned container), never here. This
        pure-transport validation (JSON decode + AST parse) stays in the
        controller rather than the use case, matching how pydantic bodies
        elsewhere in this API handle 422s.

        The axo client also sends ``code_format`` ("source", or "pickle"
        when the source couldn't stand alone -- not checked here),
        ``code_hash`` (the same code again = the same version),
        ``call_style`` ("kwargs", the only function style) and, for a
        pickle, ``display_source``: its readable text (shown on the Code
        tab and read for the run form; never run, never downloadable).
        """
        if function_id:
            # "Save as vN" on an existing function (someone else may have registered v1)
            first = functions.find_one({"function_id": function_id})
            if first is None:
                raise NotFoundError("function not found")
            if first.get("name") and first["name"] != name:
                raise HTTPException(status_code=422, detail=f"this function is named '{first['name']}'")
        if code_format not in CODE_FORMATS:
            raise HTTPException(status_code=422, detail=f"code_format must be one of {', '.join(CODE_FORMATS)}")
        parsed_runtime_spec = None
        if runtime_spec:
            try:
                parsed_runtime_spec = json.loads(runtime_spec)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=422, detail=f"runtime_spec is not valid JSON: {exc}")

        parsed_params_schema = None
        if params_schema:
            try:
                parsed_params_schema = json.loads(params_schema)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=422, detail=f"params_schema is not valid JSON: {exc}")

        source = await code.read()
        if code_format == "source":
            try:
                validate_function_source(source, name)
            except FunctionSourceError as exc:
                raise HTTPException(status_code=422, detail=str(exc))

        result = register_use_case.execute(
            current_user_id=current_user.key,
            name=name,
            code=source,
            runtime_spec=parsed_runtime_spec,
            params_schema=parsed_params_schema,
            code_format=code_format,
            code_hash=code_hash or None,
            call_style=call_style,
            display_source=display_source.encode("utf-8") if display_source else None,
            registered_by_name=current_user.name,
            function_id=function_id or None,
        )
        if defaults is not None and not result.get("existing"):
            defaults.follow_newest(result["function_id"])        # a new version becomes the default
        return result

    @router.put("/functions/{function_id}/default")
    def set_default_version(function_id: str, body: _DefaultVersionRequest,
                            current_user: ActingUser = Depends(current_user_dependency)):
        """The version that runs when none is named (the Run form, a
        choreography step without a version). Developers and above."""
        if defaults is None:
            raise HTTPException(status_code=404, detail="default versions aren't kept here")
        return defaults.set(function_id, body.version, by=current_user.key, by_name=current_user.name)

    @router.get("/functions/{function_id}/{version}/code")
    def function_code(function_id: str, version: int, _current_user: ActingUser = Depends(current_user_dependency)):
        """The version's code to read (everyone with access to the VE): its
        source, or for a pickle the readable text it was sent with
        (``downloadable`` false: the version that runs is the pickle)."""
        doc = functions.find_one({"_id": f"{function_id}:{version}"})
        if doc is None:
            raise HTTPException(status_code=404, detail="function version not found")
        if code_store is None:
            raise NotFoundError("this VE keeps no function code (no storage)", error_name="VEM_NOT_FOUND")
        is_source = doc.get("code_format", "source") == "source"
        if is_source:
            text = code_store.load(function_id, version)
        else:
            text = code_store.load_display(function_id, version)
        return {
            "function_id": function_id, "version": version, "name": doc.get("name"),
            "code_format": doc.get("code_format", "source"), "code_hash": doc.get("code_hash"),
            "text": text.decode("utf-8", errors="replace") if text is not None else None,
            "downloadable": is_source and text is not None,
            "runtime_spec": doc.get("runtime_spec"), "registered_by": doc.get("registered_by"),
            "registered_by_name": doc.get("registered_by_name"), "registered_at": doc.get("created_at"),
        }

    @router.delete("/functions/{function_id}/{version}")
    def delete_function(
        function_id: str,
        version: int,
        current_user: ActingUser = Depends(current_user_dependency),
    ):
        """Routes FUNCTION_DELETE to the function version's own recorded
        endpoint -- no endpoint_id from the caller."""
        doc = functions.find_one({"_id": f"{function_id}:{version}"})
        if doc is not None:
            _may_delete(doc, current_user)
        return delete_use_case.execute(function_id=function_id, version=version, current_user_id=current_user.key)

    @router.patch("/functions/{function_id}/{version}")
    def update_function(
        function_id: str,
        version: int,
        body: _FunctionUpdateRequest,
        current_user: ActingUser = Depends(current_user_dependency),
    ):
        """In-place params_schema/env_vars mutation, no new version, no code
        change -- same endpoint resolution as delete_function above."""
        return update_use_case.execute(
            function_id=function_id,
            version=version,
            current_user_id=current_user.key,
            params_schema=body.params_schema,
            env_vars=body.env_vars,
            runtime=body.runtime,
            env_vars_replace=body.env_vars_replace,
            secret_env=body.secret_env,
            params_schema_replace=body.params_schema_replace,
        )

    @router.delete("/functions/{function_id}/{version}/purge")
    def purge_function_version(
        function_id: str,
        version: int,
        current_user: ActingUser = Depends(current_user_dependency),
    ):
        """Hard-deletes this function version's read-model doc and its
        unified_activity history, and permanently deletes its isolated
        Kurrent stream. Distinct from delete_function above (which only
        soft-deletes via FUNCTION_DELETE/FunctionDeleted). Requires the
        version to already be soft-deleted."""
        doc = functions.find_one({"_id": f"{function_id}:{version}"})
        if doc is not None:
            _may_delete(doc, current_user)
        return purge_use_case.execute(function_id=function_id, version=version)

    return router
