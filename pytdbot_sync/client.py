import signal
import pytdbot_sync
import time

from platform import python_implementation, python_version
from os.path import join as join_path
from pathlib import Path
from getpass import getpass
from importlib import import_module

from typing import Callable, Union
from logging import getLogger, DEBUG
from base64 import b64encode
from deepdiff import DeepDiff
from concurrent.futures import ThreadPoolExecutor
from threading import Thread, current_thread, main_thread
from ujson import dumps
from queue import Queue

from .tdjson import TdJson
from .handlers import Decorators, Handler
from .methods import Methods
from .types import Plugins, Result, LogStream, Update
from .filters import Filter
from .exception import StopHandlers, AuthorizationError


logger = getLogger(__name__)


class Client(Decorators, Methods):
    """Pytdbot sync, a TDLib client

    Args:
        api_id (``int``):
            Identifier for Telegram API access, which can be obtained at https://my.telegram.org

        api_hash (``str``):
            Identifier hash for Telegram API access, which can be obtained at https://my.telegram.org

        database_encryption_key (``str`` | ``bytes``):
            Encryption key for database encryption

        files_directory (``str``):
            Directory for storing files and database

        token (``str``, *optional*):
            Bot token or phone number

        lib_path (``str``, *optional*):
            Path to TDLib library. Defaults to ``None`` (auto-detect)

        plugins (:class:`~pytdbot_sync.types.Plugins`, *optional*):
            Plugins to load

        update_class (:class:`~pytdbot_sync.types.Update`, *optional*):
            Update class to use. Defaults to :class:`~pytdbot_sync.types.Update`

        default_parse_mode (``str``, *optional*):
            The default ``parse_mode`` for methods: :meth:`~pytdbot_sync.Client.sendTextMessage`, :meth:`~pytdbot_sync.Client.sendPhoto`, :meth:`~pytdbot_sync.Client.sendAudio`, :meth:`~pytdbot_sync.Client.sendVideo`, :meth:`~pytdbot_sync.Client.sendDocument`, :meth:`~pytdbot_sync.Client.sendAnimation`, :meth:`~pytdbot_sync.Client.sendVoice`, :meth:`~pytdbot_sync.Client.sendCopy`, :meth:`~pytdbot_sync.Client.editTextMessage`; Defaults to ``None`` (Don\'t parse)
            Supported values: ``markdown``, ``markdownv2``, ``html``

        system_language_code (``str``, *optional*):
            System language code. Defaults to ``en``

        device_model (``str``, *optional*):
            Device model. Defaults to ``None`` (auto-detect)

        use_test_dc (``bool``, *optional*):
            If set to true, the Telegram test environment will be used instead of the production environment. Defaults to ``False``

        use_file_database (``bool``, *optional*):
            If set to true, information about downloaded and uploaded files will be saved between application restarts. Defaults to ``True``

        use_chat_info_database (``bool``, *optional*):
            If set to true, the library will maintain a cache of users, basic groups, supergroups, channels and secret chats. Implies ``use_file_database``. Defaults to ``True``

        use_message_database (``bool``, *optional*):
            If set to true, the library will maintain a cache of chats and messages. Implies use_chat_info_database. Defaults to ``True``

        enable_storage_optimizer (``bool``, *optional*):
            If set to true, old files will automatically be deleted. Defaults to ``True``

        ignore_file_names (``bool``, *optional*):
            If set to true, original file names will be ignored. Otherwise, downloaded files will be saved under names as close as possible to the original name. Defaults to ``False``

        options (``dict``, *optional*):
            Pass key-value dictionary to set TDLib options. Check the list of available options at https://core.telegram.org/tdlib/options

        sleep_threshold (``int``, *optional*):
            Sleep threshold for all ``FLOOD_WAIT_X`` a.k.a ``Too Many Requests: retry after`` errors occur to this client.
            If any request is rate limited (flood waited) the client will repeat the request after sleeping the required amount of seconds returned by the error. If the ``retry after`` value is higher than ``sleep_threshold`` the error is returned. Defaults to ``None`` (Disabled)

        workers (``int``, *optional*):
            Number of workers for handling updates. Defaults to ``5``

        td_verbosity (``int``, *optional*):
            Verbosity level of TDLib. Defaults to ``2``

        td_log (:class:`~pytdbot_sync.types.LogStream`, *optional*):
            Log stream. Defaults to ``None`` (Log to ``stdout``)
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        database_encryption_key: Union[str, bytes],
        files_directory: str,
        token: str = None,
        lib_path: str = None,
        plugins: Plugins = None,
        update_class: Update = Update,
        default_parse_mode: str = None,
        system_language_code: str = "en",
        device_model: str = None,
        use_test_dc: bool = False,
        use_file_database: bool = True,
        use_chat_info_database: bool = True,
        use_message_database: bool = True,
        enable_storage_optimizer: bool = True,
        ignore_file_names: bool = False,
        options: dict = None,
        sleep_threshold: int = None,
        workers: int = 5,
        td_verbosity: int = 2,
        td_log: LogStream = None,
    ) -> None:
        self.__api_id = api_id
        self.__api_hash = api_hash
        self.__token = token
        self.__database_encryption_key = database_encryption_key
        self.files_directory = files_directory
        self.lib_path = lib_path
        self.plugins = plugins
        self.update_class = update_class
        self.default_parse_mode = (
            default_parse_mode
            if isinstance(default_parse_mode, str)
            and default_parse_mode in ["markdown", "markdownv2", "html"]
            else None
        )
        self.system_language_code = system_language_code
        self.device_model = device_model
        self.use_test_dc = use_test_dc
        self.use_file_database = use_file_database
        self.use_chat_info_database = use_chat_info_database
        self.use_message_database = use_message_database
        self.enable_storage_optimizer = enable_storage_optimizer
        self.ignore_file_names = ignore_file_names
        self.td_options = options
        self.sleep_threshold = (
            sleep_threshold if isinstance(sleep_threshold, int) else 0
        )
        self.workers = ThreadPoolExecutor(
            workers if isinstance(workers, int) and workers > 0 else 5,
            "pytdbot_sync_worker",
        )
        self.queue = Queue()
        self.td_verbosity = td_verbosity
        self.connection_state: str = None
        self.is_running = None
        self.me = None
        self.is_authenticated = False
        self.options = {}

        self._check_init_args()

        self._handlers = {"initializer": [], "finalizer": []}
        self._results = {}
        self._tdjson = TdJson(lib_path, td_verbosity)
        self._retry_after_prefex = "Too Many Requests: retry after "
        self.__authorization_state = None
        self.__authorization = None
        self.__login = False
        self.__is_closing = False

        if plugins is not None:
            self._load_plugins()

        if isinstance(td_log, LogStream):
            self._tdjson.execute(
                {"@type": "setLogStream", "log_stream": td_log.to_dict()}
            )

    def __enter__(self):
        self.start()
        self.login()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.stop()
        except Exception:
            pass

    @property
    def authorization_state(self) -> str:
        """Current authorization state"""
        return self.__authorization_state

    def start(self, login: bool = True) -> None:
        """Start Pytdbot sync client

        Args:
            login (``bool``, *optional*):
                Login after start. Defaults to ``True``
        """
        if not self.is_running:

            logger.info("Starting Pytdbot sync client...")

            Thread(target=self.__listen_loop, daemon=True).start()

            logger.info("Started with %s workers", self.workers._max_workers)

        if login:
            self.login()

    def login(self) -> None:
        """Login to Telegram."""

        if self.is_authenticated:
            return

        self.__login = True

        self.getOption("version")  # Ping TDLib to start authorization proccess

        while self.authorization_state != "authorizationStateReady":
            time.sleep(0.1)
            if self.authorization_state == "authorizationStateClosed":
                return

        if not self.is_running:
            return

        self.me = self.getMe()
        if self.me.is_error:
            logger.error("Get me error: {}".format(self.me["message"]))

        self.me = self.me.result
        self.is_authenticated = True
        logger.info(
            "Logged in as {} {}".format(
                self.me["first_name"],
                self.me["id"].__str__()
                if "usernames" not in self.me
                else "@" + self.me["usernames"]["editable_username"],
            )
        )

    def add_handler(
        self,
        update_type: str,
        func: Callable,
        filters: pytdbot_sync.filters.Filter = None,
        position: int = None,
    ) -> None:
        """Add an update handler

        Args:
            update_type (``str``):
                An update type

            func (``Callable``):
                A callable function

            filters (:class:`~pytdbot_sync.filters.Filter`, *optional*):
                message filter

            position (``int``, *optional*):
                The function position in handlers list. Defaults to ``None`` (append)

        Raises:
            TypeError
        """
        if not isinstance(update_type, str):
            raise TypeError("update_type must be str")
        elif not isinstance(func, Callable):
            raise TypeError("func must be callable")
        elif filters is not None and not isinstance(filters, Filter):
            raise TypeError("filters must be instance of pytdbot_sync.filters.Filter")
        else:
            func = Handler(func, update_type, filters, position)
            if update_type not in self._handlers:
                self._handlers[update_type] = []
            if isinstance(position, int):
                self._handlers[update_type].insert(position, func)
            else:
                self._handlers[update_type].append(func)
        self._handlers[update_type].sort(key=lambda x: (x.position is None, x.position))

    def remove_handler(self, func: Callable) -> bool:
        """Remove an update handler

        Args:
            func (``Callable``):
                A callable function

        Raises:
            TypeError

        Returns:
            :py:class:`bool`: True if handler was removed, False otherwise
        """
        if not isinstance(func, Callable):
            raise TypeError("func must be callable")
        for update_type in self._handlers:
            for handler in self._handlers[update_type]:
                if handler.func == func:
                    self._handlers[update_type].remove(handler)
                    self._handlers[update_type].sort(
                        key=lambda x: (x.position is None, x.position)
                    )
                    return True
        return False

    def invoke(
        self,
        request: dict,
    ) -> Result:
        """Invoke a new TDLib request

        Example:
            .. code-block:: python

                from pytdbot_sync import Client

                with Client(...) as client:
                    res = await client.invoke({"@type": "getOption", "name": "version"})
                    if not res.is_error:
                        print(res)

        Args:
            request (``dict``):
                The request to be sent

        Returns:
            :class:`~pytdbot_sync.types.Result`
        """

        result = Result(request)
        self._results[result.id] = result

        if (
            logger.root.level >= DEBUG
        ):  # dumping all requests may create performance issues
            logger.debug("Sending: {}".format(dumps(result.request, indent=4)))

        self.__send(result.request)
        result.wait()

        if result.is_error:
            if result["code"] == 429:
                retry_after = self.get_retry_after_time(result["message"])

                if retry_after <= self.sleep_threshold:
                    result.reset()

                    logger.error(
                        "Sleeping for {}s (Caused by {})".format(
                            retry_after, result.request["@type"]
                        )
                    )

                    time.sleep(retry_after)
                    self._results[result.id] = result
                    self.__send(result.request)
                    result.wait()
            elif not self.use_message_database and (
                result["code"] == 400
                and result["message"] == "Chat not found"
                and "chat_id" in result.request
            ):
                chat_id = result.request["chat_id"]

                logger.debug("Attempt to load chat {}".format(chat_id))

                load_chat = self.getChat(chat_id)

                if not load_chat.is_error:
                    logger.debug("Chat {} is loaded".format(chat_id))

                    message_id = 0
                    if "reply_to_message_id" in result.request:
                        message_id = result.request["reply_to_message_id"]
                    elif "message_id" in result.request:
                        message_id = result.request["message_id"]

                    # If there is a message_id then
                    # we need to load it to avoid MESSAGE_NOT_FOUND
                    if message_id > 0:
                        self.getMessage(chat_id, message_id)

                    # repeat the first request
                    result.reset()
                    self._results[result.id] = result
                    self.__send(result.request)
                    result.wait()
                else:
                    logger.error("Couldn't load chat {}".format(chat_id))

        return result

    def call_method(self, method: str, **kwargs) -> Result:
        """Call a method. with keyword arguments (``kwargs``) support

        Example:
            .. code-block:: python

                from pytdbot_sync import Client

                with Client(...) as client:
                    res = await client.call_method("getOption", name="version"})
                    if not res.is_error:
                        print(res)

        Args:
            method (``str``):
                TDLib method name

        Returns:
            :class:`~pytdbot_sync.types.Result`
        """

        kwargs["@type"] = method

        return self.invoke(kwargs)

    def run(self, login: bool = True) -> None:
        """Start the client and block until the client is stopped

        Example:
            .. code-block:: python

                from pytdbot_sync import Client

                client = Client(...)

                @client.on_updateNewMessage()
                def new_message(c,update):
                    await update.reply_text('Hello!')

                client.run()

        Args:
            login (``bool``, *optional*):
                Login after start. Defaults to ``True``
        """

        self._register_signal_handlers()

        self.start(login)
        self.idle()

    def idle(self):
        """Idle and wait until the client is stopped."""

        while self.is_running:
            time.sleep(1)

    def stop(self) -> bool:
        """Stop the client

        Raises:
            `RuntimeError`:
                If the instance is already stopped

        Returns:
            :py:class:`bool`: ``True`` on success
        """
        if (
            self.is_running is False
            and self.authorization_state == "authorizationStateClosed"
        ):
            raise RuntimeError("Instance is not running")

        logger.info("Waiting for TDLib to close...")

        self.__is_closing = True

        self.close()

        while self.authorization_state != "authorizationStateClosed":
            time.sleep(0.1)
        else:
            self.__stop_client()

            logger.info("Instance closed")

            return True

    def __send(self, request: dict) -> None:
        return self._tdjson.send(
            request
        )  # tdjson.send is asynchronous, So we don't need run_in_executor. This improves performance

    def __receive(self, timeout: float = 2.0) -> dict:
        # return await self.loop.run_in_executor(
        #     self._executor, self._tdjson.receive, timeout
        # )
        return self._tdjson.receive(timeout)

    def _check_init_args(self):
        if not isinstance(self.__api_id, int):
            raise TypeError("api_id must be int")
        elif not isinstance(self.__api_hash, str):
            raise TypeError("api_hash must be str")
        elif not isinstance(self.__database_encryption_key, str) and not isinstance(
            self.__database_encryption_key, bytes
        ):
            raise TypeError("database_encryption_key must be str or bytes")
        elif not isinstance(self.files_directory, str):
            raise TypeError("files_directory must be str")
        elif not isinstance(self.td_verbosity, int):
            raise TypeError("td_verbosity must be int")
        elif type(Update) is not type(self.update_class):
            raise TypeError(
                "update_class must be instance of class pytdbot_sync.types.Update"
            )

    def get_retry_after_time(self, error_message: str) -> int:
        """Get the retry after time from flood wait error message

        Args:
            error_message (``str``):
                The returned error message from TDLib

        Returns:
            py:class:`int`
        """

        try:
            return int(error_message.removeprefix(self._retry_after_prefex))
        except Exception:
            return 0

    def _load_plugins(self):
        count = 0
        handlers = 0
        for path in sorted(Path(self.plugins.folder).rglob("*.py")):
            module_path = ".".join(path.parent.parts + (path.stem,))
            try:
                module = import_module(module_path)
            except Exception:
                logger.exception("Failed to load plugin {}".format(module_path))
            else:
                logger.debug("Plugin {} loaded".format(module_path))
                for name in dir(module):
                    obj = getattr(module, name)
                    if hasattr(obj, "_handler") and isinstance(obj._handler, Handler):
                        self.add_handler(
                            obj._handler.update_type,
                            obj._handler.func,
                            obj._handler.filter,
                            obj._handler.position,
                        )
                        logger.debug(
                            "Handler {} added from {}".format(
                                obj._handler.func,
                                module_path,
                            )
                        )
                        handlers += 1

                count += 1
        logger.info("From {} plugins got {} handlers".format(count, handlers))

    def __listen_loop(self):
        try:
            self.is_running = True
            logger.info("Listening to updates...")

            while self.is_running:
                update = self.__receive(100000.0)  # seconds
                if update is None:
                    continue
                self._process_update(update)

        except Exception:
            logger.exception("Exception in __listen_loop")
        finally:
            self.is_running = False

    def _process_update(self, update):
        if "@client_id" in update:
            del update["@client_id"]

        if "@type" not in update:
            logger.error("Unexpected update received: {}".format(update))
            return
        elif "@extra" in update:
            if (
                logger.root.level >= DEBUG
            ):  # dumping all results may create performance issues
                logger.debug("Recieved: {}".format(dumps(update, indent=4)))
            if update["@extra"]["id"] in self._results:
                result: Result = self._results.pop(update["@extra"]["id"])
                result.set_result(update)
            elif update["@type"] == "error" and "option" in update["@extra"]:
                logger.error(
                    "{}: {}".format(
                        update["@extra"]["option"],
                        update["message"],
                    )
                )
        else:
            if update["@type"] == "updateAuthorizationState":
                self.workers.submit(self.__handle_authorization_state, update)
            elif update["@type"] == "updateMessageSendSucceeded":
                self.__handle_update_message_succeeded(update)
            elif update["@type"] == "updateMessageSendFailed":
                self.__handle_update_message_failed(update)
            elif update["@type"] == "updateConnectionState":
                self.__handle_connection_state(update)
            elif update["@type"] == "updateOption":
                self.__handle_update_option(update)
            elif update["@type"] == "updateUser":
                self.__handle_update_user(update)

            self.workers.submit(self._update_worker, update)

    def __run_initializers(self, update):
        for initializer in self._handlers["initializer"]:
            try:
                if initializer.filter is not None:
                    filter_func = initializer.filter.func

                    if not filter_func(self, update):
                        continue

                initializer.func(self, update)
            except StopHandlers as e:
                raise e
            except Exception:
                logger.exception("Initializer {} failed".format(initializer))

    def __run_handlers(self, update):
        update_type = update["@type"]
        for handler in self._handlers[update_type]:
            try:
                if handler.filter is not None:
                    filter_func = handler.filter.func

                    if not filter_func(self, update):
                        continue

                handler.func(self, update)
            except StopHandlers as e:
                raise e
            except Exception:
                logger.exception("Exception in {}".format(handler))

    def __run_finalizers(self, update):
        for finalizer in self._handlers["finalizer"]:
            try:
                if finalizer.filter is not None:
                    filter_func = finalizer.filter.func

                    if not filter_func(self, update):
                        continue

                finalizer.func(self, update)
            except StopHandlers as e:
                raise e
            except Exception:
                logger.exception("Finalizer {} failed".format(finalizer))

    def _update_worker(self, update):
        if self.is_running:
            try:
                if "@type" not in update:
                    return

                if (
                    logger.root.level >= DEBUG
                ):  # dumping all updates can create performance issues
                    logger.debug(
                        "Received: {}".format(dumps(update, indent=4)),
                    )

                if update["@type"] in self._handlers:

                    update = self.update_class(self, update)
                    if (
                        update["@type"] == "updateNewMessage"
                        and update["message"]["is_outgoing"]
                        and "sending_state" in update["message"]
                    ):
                        return

                    try:
                        self.__run_initializers(update)
                        self.__run_handlers(update)
                    except StopHandlers:
                        pass
                    finally:
                        self.__run_finalizers(update)
            except Exception:
                logger.exception("Exception in _update_worker")

    def set_td_paramaters(self):
        """Make a call to :meth:`~pytdbot_sync.Client.setTdlibParameters` with the current client init parameters

        Raises:
            `AuthorizationError`
        """
        if isinstance(self.__database_encryption_key, str):
            self.__database_encryption_key = self.__database_encryption_key.encode(
                "utf-8"
            )

        res = self.setTdlibParameters(
            use_test_dc=self.use_test_dc,
            api_id=self.__api_id,
            api_hash=self.__api_hash,
            system_language_code=self.system_language_code,
            device_model=f"{python_implementation()} {python_version()}",
            use_file_database=self.use_file_database,
            use_chat_info_database=self.use_chat_info_database,
            use_message_database=self.use_message_database,
            use_secret_chats=False,
            system_version=None,
            enable_storage_optimizer=self.enable_storage_optimizer,
            ignore_file_names=self.ignore_file_names,
            files_directory=self.files_directory,
            database_encryption_key=b64encode(self.__database_encryption_key).decode(
                "utf-8"
            ),
            database_directory=join_path(self.files_directory, "database"),
            application_version=f"Pytdbot sync {pytdbot_sync.__version__}",
        )
        if res.is_error:
            raise AuthorizationError(res.result["message"])

    def _set_bot_token(self):
        res = self.checkAuthenticationBotToken(self.__token)
        if res.is_error:
            raise AuthorizationError(res.result["message"])

    def _set_options(self):
        if not isinstance(self.td_options, dict):
            return

        for k, v in self.td_options.items():
            v_type = type(v)

            if v_type is str:
                data = {"@type": "optionValueString", "value": v}
            elif v_type is int:
                data = {"@type": "optionValueInteger", "value": v}
            elif v_type is bool:
                data = {"@type": "optionValueBoolean", "value": v}
            else:
                raise ValueError(f"Option {k} has unsupported type {v_type}")

            self.__send(
                {
                    "@type": "setOption",
                    "name": k,
                    "value": data,
                    "@extra": {"option": k, "value": v, "id": ""},
                }
            )
            logger.debug("Option {} sent with value {}".format(k, str(v)))

    def __handle_authorization_state(self, update):
        if update["@type"] == "updateAuthorizationState":
            old_authorization_state = self.authorization_state
            self.__authorization_state = update["authorization_state"]["@type"]
            self.__authorization = update["authorization_state"]

            logger.info(
                "Authorization state changed to {}".format(
                    self.authorization_state.removeprefix("authorizationState"),
                )
            )

            if self.__login:
                if self.authorization_state == "authorizationStateWaitTdlibParameters":
                    self._set_options()
                    self.set_td_paramaters()
                elif self.authorization_state == "authorizationStateWaitPhoneNumber":
                    self._print_welcome()
                    self.__handle_authorization_state_wait_phone_number()
                elif self.authorization_state == "authorizationStateWaitEmailAddress":
                    self.__handle_authorization_state_wait_email_address()
                elif self.authorization_state == "authorizationStateWaitEmailCode":
                    self.__handle_authorization_state_wait_email_code()
                elif self.authorization_state == "authorizationStateWaitCode":
                    self.__handle_authorization_state_wait_code()
                elif self.authorization_state == "authorizationStateWaitRegistration":
                    self.__handle_authorization_state_wait_registration()
                elif (
                    old_authorization_state != "authorizationStateWaitPassword"
                    and self.authorization_state == "authorizationStateWaitPassword"
                ):
                    self.__handle_authorization_state_wait_password()
                elif (
                    self.authorization_state == "authorizationStateClosed"
                    and self.__is_closing is False
                ):
                    self.__stop_client()

    def __handle_connection_state(self, update):
        if update["@type"] == "updateConnectionState":
            self.connection_state: str = update["state"]["@type"]
            logger.info(
                "Connection state changed to {}".format(
                    self.connection_state.removeprefix("connectionState"),
                )
            )

    def __handle_update_message_succeeded(self, update):
        m_id = (
            update["old_message_id"].__str__() + update["message"]["chat_id"].__str__()
        )

        if m_id in self._results:
            result: Result = self._results.pop(m_id)
            result.set_result(update["message"])

    def __handle_update_message_failed(self, update):
        m_id = (
            update["old_message_id"].__str__() + update["message"]["chat_id"].__str__()
        )

        if m_id in self._results:
            if update["error_code"] == 429:
                retry_after = update["message"]["sending_state"]["retry_after"]

                if retry_after <= self.sleep_threshold:
                    result: Result = self._results.pop(m_id)

                    logger.error(
                        "Sleeping for {}s (Caused by {})".format(
                            int(retry_after), result.request["@type"]
                        )
                    )

                    time.sleep(retry_after)
                    res = self.invoke(result.request)

                    self._results[
                        res.result["id"].__str__()
                        + update["message"]["chat_id"].__str__()
                    ] = result
            else:
                result: Result = self._results.pop(m_id)
                result.set_result(
                    {
                        "@type": "error",
                        "code": update["error_code"],
                        "message": update["error_message"],
                    }
                )

    def __handle_update_option(self, update):

        if update["value"]["@type"] == "optionValueBoolean":
            self.options[update["name"]] = bool(update["value"]["value"])
        elif update["value"]["@type"] == "optionValueEmpty":
            self.options[update["name"]] = None
        elif update["value"]["@type"] == "optionValueInteger":
            self.options[update["name"]] = int(update["value"]["value"])
        else:
            self.options[update["name"]] = update["value"]["value"]

        if self.is_authenticated:
            logger.info(
                "Option {} changed to {}".format(
                    update["name"],
                    self.options[update["name"]],
                )
            )

    def __handle_update_user(self, update):
        if self.is_authenticated and update["user"]["id"] == self.me["id"]:
            logger.info(
                "Updating {} ({}) info".format(
                    self.me["first_name"],
                    self.me["id"].__str__()
                    if "usernames" not in self.me
                    else "@" + self.me["usernames"]["editable_username"],
                )
            )
            try:
                deepdiff(self.me, update["user"])
            except Exception:
                logger.exception("deepdiff failed")
            self.me = update["user"]

    def __handle_authorization_state_wait_phone_number(self):
        if self.authorization_state != "authorizationStateWaitPhoneNumber":
            return

        if not isinstance(self.__token, str):
            while self.is_running:
                user_input = input("Enter a phone number or bot token: ")

                if user_input:
                    y_n = input(
                        'Is "{}" correct? (y/n): '.format(user_input),
                    )

                    if y_n == "" or y_n.lower() in ["y", "yes"]:
                        if ":" in user_input:
                            res = self.checkAuthenticationBotToken(user_input)
                        else:
                            res = self.setAuthenticationPhoneNumber(user_input)

                        if res.is_error:
                            print(res["message"])
                        else:
                            break
        else:
            if ":" in self.__token:
                res = self.checkAuthenticationBotToken(self.__token)
            else:
                res = self.setAuthenticationPhoneNumber(self.__token)

            if res.is_error:
                raise AuthorizationError(res["message"])

    def __handle_authorization_state_wait_email_address(self):
        if self.authorization_state == "authorizationStateWaitEmailAddress":
            return

        while self.is_running:
            email_address = input("Enter your email address: ")

            res = self.setAuthenticationEmailAddress(email_address)
            if res.is_error:
                print(res["message"])
            else:
                break

    def __handle_authorization_state_wait_email_code(self):
        if self.authorization_state != "authorizationStateWaitEmailCode":
            return

        while self.is_running:
            code = input(
                "Enter the email authentication code you received: ",
            )

            res = self.checkAuthenticationEmailCode(
                code={"@type": "emailAddressAuthenticationCode", "code": code}
            )
            if res.is_error:
                print(res["message"])
            else:
                break

    def __handle_authorization_state_wait_code(self):
        if self.authorization_state != "authorizationStateWaitCode":
            return

        code_type = self.__authorization["code_info"]["type"]["@type"]

        if code_type == "authenticationCodeTypeTelegramMessage":
            code_type = "Telegram app"
        elif code_type == "authenticationCodeTypeSms":
            code_type = "SMS"
        elif code_type == "authenticationCodeTypeCall":
            code_type = "phone call"
        elif code_type == "authenticationCodeTypeFlashCall":
            code_type = "phone flush call"
        elif code_type == "authenticationCodeTypeMissedCall":
            code_type = "phone missed call"
        elif code_type == "authenticationCodeTypeFragment":
            code_type = "fragment.com SMS"

        while self.is_running:
            code = input(
                "Enter the login code received via {}: ".format(code_type),
            )

            res = self.checkAuthenticationCode(code=code)
            if res.is_error:
                print(res["message"])
            else:
                break

    def __handle_authorization_state_wait_registration(self):
        if self.authorization_state != "authorizationStateWaitRegistration":
            return

        while self.is_running:
            first_name = input("Enter your first name: ")
            last_name = input("Enter your last name: ")

            res = self.registerUser(first_name=first_name, last_name=last_name)
            if res.is_error:
                print(res["message"])
            else:
                break

    def __handle_authorization_state_wait_password(self):
        if self.authorization_state != "authorizationStateWaitPassword":
            return

        if self.__authorization["password_hint"]:
            print(
                "Your 2FA password hint is: {}".format(
                    self.__authorization["password_hint"]
                )
            )

        while self.is_running:
            password = (
                getpass(
                    "Enter your 2FA password {}: ".format(
                        "(empty to recover)"
                        if self.__authorization["has_recovery_email_address"]
                        else ""
                    )
                ),
            )

            if password == "":
                if self.__authorization["has_recovery_email_address"]:
                    y_n = input(
                        "Are you sure you want to recover your 2FA password? (y/n): ",
                    )

                    if y_n.lower() in ["y", "yes"]:
                        res = self.requestAuthenticationPasswordRecovery()

                        if res.is_error:
                            raise AuthorizationError(res["message"])
                        else:
                            while True:
                                recovery_code = input(
                                    "Enter your recovery code sent to {}: ".format(
                                        self.__authorization[
                                            "recovery_email_address_pattern"
                                        ]
                                    ),
                                )

                                res = self.checkAuthenticationPasswordRecoveryCode(
                                    recovery_code
                                )

                                if res.is_error:
                                    print(res["message"])
                                else:
                                    recover_res = self.recoverAuthenticationPassword(
                                        recovery_code
                                    )
                                    if recover_res.is_error:
                                        raise AuthorizationError(recover_res["message"])

                                    return
                else:
                    print(
                        "You can't recover your 2FA password because you don't set any recovery email address"
                    )
            else:
                res = self.checkAuthenticationPassword(password)
                if res.is_error:
                    print(res["message"])
                else:
                    break

    def __stop_client(self) -> None:
        self.is_authenticated = False
        self.is_running = False

        self.workers.shutdown(wait=False, cancel_futures=True)

    def _register_signal_handlers(self):
        def _handle_signal(sig_num, frame):
            self.stop()

        if current_thread() is main_thread():

            for sig in (
                signal.SIGINT,
                signal.SIGTERM,
                signal.SIGABRT,
                signal.SIGSEGV,
            ):
                signal.signal(sig, _handle_signal)

    def _print_welcome(self):
        print(
            "Welcome to Pytdbot sync (v{}). {}".format(
                pytdbot_sync.__version__, pytdbot_sync.__copyright__
            )
        )
        print(
            "Pytdbot sync is free software and comes with ABSOLUTELY NO WARRANTY. Licensed under the terms of {}.\n\n".format(
                pytdbot_sync.__license__
            )
        )


def deepdiff(d1, d2):
    if not isinstance(d1, dict) or not isinstance(d2, dict):
        return d1 == d2

    deep = DeepDiff(d1, d2, ignore_order=True, view="tree")

    for parent in deep.keys():
        for diff in deep[parent]:
            difflist = diff.path(output_format="list")
            key = ".".join(v.__str__() for v in difflist)

            if parent in ["dictionary_item_added", "values_changed"]:
                logger.info(f"{key} changed to {diff.t2}")
            elif parent == "dictionary_item_removed":
                logger.info(f"{key} removed")
