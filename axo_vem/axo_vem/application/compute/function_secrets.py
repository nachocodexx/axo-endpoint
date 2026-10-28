"""The values of a function's secret env vars, for the VEM's own use only.

A secret's value goes to the endpoints (they run the function with it) and
is kept here so Restoring can register the function again after the VE
stops and starts. It never leaves the VEM: events, the read model and every
API answer carry the secret's name with no value.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pymongo.collection import Collection


def masked(runtime_spec: Optional[Dict[str, Any]], show_values: bool = True) -> Optional[Dict[str, Any]]:
    """The spec as it may be shown: secret values (or, for a Viewer, every
    value) left out."""
    if not runtime_spec or "env_vars" not in runtime_spec:
        return runtime_spec
    secret = set(runtime_spec.get("secret_env") or [])
    env = runtime_spec.get("env_vars") or {}
    return {**runtime_spec, "env_vars": {k: (v if show_values and k not in secret else None) for k, v in env.items()}}


class FunctionSecrets:
    def __init__(self, collection: Collection) -> None:
        self._collection = collection
        self._collection.create_index("function_id")

    @staticmethod
    def _id(function_id: str, version: int) -> str:
        return f"{function_id}:{version}"

    def keep(self, function_id: str, version: int, runtime_spec: Optional[Dict[str, Any]],
             replace: bool = False) -> None:
        """Saves the secret values the spec carries (a secret sent without a
        value keeps its saved one); ``replace`` drops the ones no longer secret."""
        names = list((runtime_spec or {}).get("secret_env") or [])
        sent = {k: v for k, v in ((runtime_spec or {}).get("env_vars") or {}).items() if k in names and v is not None}
        saved = self.values(function_id, version)
        values = {**({k: v for k, v in saved.items() if k in names} if replace else saved), **sent}
        self._collection.update_one({"_id": self._id(function_id, version)},
                                    {"$set": {"function_id": function_id, "version": version, "values": values}},
                                    upsert=True)

    def values(self, function_id: str, version: int) -> Dict[str, str]:
        doc = self._collection.find_one({"_id": self._id(function_id, version)})
        return dict((doc or {}).get("values") or {})

    def filled(self, function_id: str, version: int, runtime_spec: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """The spec with its secret values back in (for registering it again)."""
        if not runtime_spec or not runtime_spec.get("secret_env"):
            return runtime_spec
        values = self.values(function_id, version)
        env = {**(runtime_spec.get("env_vars") or {}), **values}
        return {**runtime_spec, "env_vars": {k: v for k, v in env.items() if v is not None}}

    def delete(self, function_id: str, version: Optional[int] = None) -> None:
        if version is None:
            self._collection.delete_many({"function_id": function_id})
        else:
            self._collection.delete_one({"_id": self._id(function_id, version)})
