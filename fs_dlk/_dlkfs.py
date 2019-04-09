import contextlib
import threading

from itertools import chain

from fs import errors
from fs import ResourceType
from fs.base import FS
from fs.info import Info
from fs.path import basename, normpath, relpath, forcedir

import azure.datalake.store as az_store
import azure.datalake.store.exceptions as client_error


@contextlib.contextmanager
def dlkerrors(path):
    """ Translate Datalake errors to FSErrors.

        FS errors: https://docs.pyfilesystem.org/en/latest/reference/errors.html
        DLK errors: https://docs.pyfilesystem.org/en/latest/reference/errors.html
    """
    try:
        yield
    except client_error.FileNotFoundError as error:
        raise errors.ResourceNotFound(path, exc=error)
    except client_error.FileExistsError as error:
        raise errors.FileExists(path, exc=error)
    except client_error.PermissionError as error:
        raise errors.PermissionDenied(path, exc=error)
    except client_error.DatalakeBadOffsetException as error:
        raise errors.RemoteConnectionError(path, exc=error, msg="DatalakeBadOffsetException")
    except client_error.DatalakeIncompleteTransferException as error:
        raise errors.RemoteConnectionError(path, exc=error, msg="DatalakeIncompleteTransferException")
    except client_error.DatalakeRESTException as error:
        raise errors.RemoteConnectionError(path, exc=error, msg="DatalakeRESTException")


class DLKFS(FS):
    def __init__(
            self,
            dir_path="/",
            client_id=None,
            client_secret=None,
            tenant_id=None,
            store=None
    ):
        self._prefix = relpath(normpath(dir_path)).rstrip("/")
        self._tlocal = threading.local()
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self.store_name = store
        super(DLKFS, self).__init__()

    @property
    def dlk(self):
        if not hasattr(self._tlocal, "dlk"):
            token = az_store.lib.auth(self.tenant_id, client_id=self.client_id, client_secret=self.client_secret)
            self._tlocal.dlk = az_store.core.AzureDLFileSystem(
                token,
                store_name=self.store_name
            )
        return self._tlocal.dlk

    def getinfo(self, path, namespaces=None):
        self.check()
        namespaces = namespaces or ()
        _path = self.validatepath(path)
        _key = self._path_to_key(_path)

        if _path == "/":
            return Info(
                {
                    "basic": {"name": "", "is_dir": True},
                    "details": {"type": int(ResourceType.directory)},
                }
            )

        info = None
        try:
            with dlkerrors(path):
                info = self.dlk.info(_key)
        except errors.ResourceNotFound:
            raise errors.ResourceNotFound(path)

        info_dict = self._info_from_object(info, namespaces)
        return Info(info_dict)

    def _path_to_key(self, path):
        """Converts an fs path to a datalake path."""
        _path = relpath(normpath(path))
        _key = (
            "{}/{}".format(self._prefix, _path).lstrip("/")
        )
        return _key

    def _path_to_dir_key(self, path):
        """Converts an fs path to a Datalake dir path."""
        _path = relpath(normpath(path))
        _key = (
            forcedir("{}/{}".format(self._prefix, _path))
            .lstrip("/")
        )
        return _key

    def _key_to_path(self, key):
        return key

    def _info_from_object(self, obj, namespaces):
        """ Make an info dict from a Datalake info() return.

            List of functional namespaces: https://github.com/PyFilesystem/pyfilesystem2/blob/master/fs/info.py
        """
        key = obj['name']
        path = self._key_to_path(key)
        name = basename(path.rstrip("/"))
        is_dir = obj.get("type", "") == "DIRECTORY"
        info = {"basic": {"name": name, "is_dir": is_dir}}

        details_mapping = {
            "accessed": "accessTime",
            "modified": "modificationTime",
            "size": "blockSize"
        }
        if "details" in namespaces:
            _type = int(ResourceType.directory if is_dir else ResourceType.file)
            details_info = {
                "type": _type
            }
            for info_key, dlk_key in details_mapping.items():
                details_info[info_key] = obj[dlk_key]
            info["details"] = details_info

        access_mapping = {
            "owner": "owner",
            "group": "group",
            "permission": "permission"
        }
        if "access" in namespaces:
            access_info = dict()
            for info_key, dlk_key in access_mapping.items():
                access_info[info_key] = obj[dlk_key]
            info["access"] = access_info

        if "dlk" in namespaces:
            dlk_info = dict(obj)
            for parsed_key in chain(details_mapping.values(),
                                    access_mapping.values()):
                if parsed_key in dlk_info:
                    del dlk_info[parsed_key]
            info["dlk"] = dlk_info

        return info

    def listdir(self, path):
        _path = self.validatepath(path)
        _key = self._path_to_key(_path)
        prefix_len = len(_key)

        with dlkerrors(path):
            entries = self.dlk.ls(_key, detail=True)

        def format_dir(path):
            nameonly = path[prefix_len:]
            if nameonly.startswith('/'):
                nameonly = nameonly[1:]
            return forcedir(nameonly)

        dirs = [format_dir(e['name']) for e in entries if e['type'] == 'DIRECTORY']
        files = [basename(e['name']) for e in entries if e['type'] != 'DIRECTORY']
        return sorted(dirs) + sorted(files)

    def makedir(self, path, permissions=None, recreate=False):
        raise NotImplementedError()

    def openbin(self, path, mode="r", buffering=-1, **options):
        raise NotImplementedError()

    def remove(self, path):
        raise NotImplementedError()

    def removedir(self, path):
        raise NotImplementedError()

    def setinfo(self, path, info):
        self.getinfo(path)