"""
Flask backend

.. note::
    在 AsyncBasedSession 会话中，若在协程任务函数内调用 asyncio 中的协程函数，需要使用 asyncio_coroutine


.. attention::
    PyWebIO 的会话状态保存在进程内，所以不支持多进程部署的Flask。
        比如使用 ``uWSGI`` 部署Flask，并使用 ``--processes n`` 选项设置了多进程；
        或者使用 ``nginx`` 等反向代理将流量负载到多个 Flask 副本上。

    A note on run Flask with uWSGI：

    If you start uWSGI without threads, the Python GIL will not be enabled,
    so threads generated by your application will never run. `uWSGI doc <https://uwsgi-docs.readthedocs.io/en/latest/WSGIquickstart.html#a-note-on-python-threads>`_
    在Flask backend中，PyWebIO使用单独一个线程来运行事件循环。如果程序中没有使用到asyncio中的协程函数，
    可以在 start_flask_server 参数中设置 ``disable_asyncio=False`` 来关闭对asyncio协程函数的支持。
    如果您需要使用asyncio协程函数，那么需要在在uWSGI中使用 ``--enable-thread`` 选项开启线程支持。

"""
import asyncio
import fnmatch
import threading
import time
from functools import partial
from typing import Dict

from flask import Flask, request, jsonify, send_from_directory, Response

from ..session import CoroutineBasedSession, get_session_implement, AbstractSession, \
    set_session_implement_for_target
from ..utils import STATIC_PATH
from ..utils import random_str, LRUDict

# todo: use lock to avoid thread race condition
_webio_sessions: Dict[str, AbstractSession] = {}  # WebIOSessionID -> WebIOSession()
_webio_expire = LRUDict()  # WebIOSessionID -> last active timestamp

DEFAULT_SESSION_EXPIRE_SECONDS = 60  # 超过60s会话不活跃则视为会话过期
REMOVE_EXPIRED_SESSIONS_INTERVAL = 20  # 清理过期会话间隔（秒）
WAIT_MS_ON_POST = 100  # 在处理完POST请求时，等待WAIT_MS_ON_POST毫秒再读取返回数据。Task的command可以立即返回

_event_loop = None


def _make_response(webio_session: AbstractSession):
    return jsonify(webio_session.get_task_commands())


def _remove_expired_sessions(session_expire_seconds):
    while _webio_expire:
        sid, active_ts = _webio_expire.popitem(last=False)
        if time.time() - active_ts < session_expire_seconds:
            _webio_expire[sid] = active_ts
            _webio_expire.move_to_end(sid, last=False)
            break
        del _webio_sessions[sid]


_last_check_session_expire_ts = 0  # 上次检查session有效期的时间戳


def _remove_webio_session(sid):
    del _webio_sessions[sid]
    del _webio_expire[sid]


def cors_headers(origin, check_origin, headers=None):
    if headers is None:
        headers = {}

    if check_origin(origin):
        headers['Access-Control-Allow-Origin'] = origin
        headers['Access-Control-Allow-Methods'] = 'GET, POST'
        headers['Access-Control-Allow-Headers'] = 'content-type, webio-session-id'
        headers['Access-Control-Expose-Headers'] = 'webio-session-id'
        headers['Access-Control-Max-Age'] = 1440 * 60

    return headers


def _webio_view(target, session_expire_seconds, check_origin):
    """
    :param target:
    :param session_expire_seconds:
    :return:
    """
    global _last_check_session_expire_ts, _event_loop
    if _event_loop:
        asyncio.set_event_loop(_event_loop)

    if request.method == 'OPTIONS':  # preflight request for CORS
        headers = cors_headers(request.headers.get('Origin', ''), check_origin)
        return Response('', headers=headers, status=204)

    headers = {}

    if request.headers.get('Origin'):  # set headers for CORS request
        headers = cors_headers(request.headers.get('Origin'), check_origin, headers=headers)

    if request.args.get('test'):  # 测试接口，当会话使用给予http的backend时，返回 ok
        return Response('ok', headers=headers)

    webio_session_id = None
    if 'webio-session-id' not in request.headers or not request.headers['webio-session-id']:  # start new WebIOSession
        webio_session_id = random_str(24)
        headers['webio-session-id'] = webio_session_id
        Session = get_session_implement()
        webio_session = Session(target)
        _webio_sessions[webio_session_id] = webio_session
        _webio_expire[webio_session_id] = time.time()
    elif request.headers['webio-session-id'] not in _webio_sessions:  # WebIOSession deleted
        return jsonify([dict(command='close_session')])
    else:
        webio_session_id = request.headers['webio-session-id']
        webio_session = _webio_sessions[webio_session_id]

    if request.method == 'POST':  # client push event
        webio_session.send_client_event(request.json)
        time.sleep(WAIT_MS_ON_POST / 1000.0)
    elif request.method == 'GET':  # client pull messages
        pass

    # clean up at intervals
    if time.time() - _last_check_session_expire_ts > REMOVE_EXPIRED_SESSIONS_INTERVAL:
        _remove_expired_sessions(session_expire_seconds)
        _last_check_session_expire_ts = time.time()

    response = _make_response(webio_session)

    if webio_session.closed():
        _remove_webio_session(webio_session_id)

    # set header to response
    for k, v in headers.items():
        response.headers[k] = v

    return response


def webio_view(target, session_expire_seconds=DEFAULT_SESSION_EXPIRE_SECONDS, allowed_origins=None, check_origin=None):
    """获取用于与Flask进行整合的view函数

    :param target: 任务函数。任务函数为协程函数时，使用 :ref:`基于协程的会话实现 <coroutine_based_session>` ；任务函数为普通函数时，使用基于线程的会话实现。
    :param list allowed_origins: 除当前域名外，服务器还允许的请求的来源列表。
    :param session_expire_seconds: 会话不活跃过期时间。
    :param list allowed_origins: 除当前域名外，服务器还允许的请求的来源列表。
        来源包含协议和域名和端口部分，允许使用 ``*`` 作为通配符。 比如 ``https://*.example.com`` 、 ``*://*.example.com`` 、
    :param callable check_origin: 请求来源检查函数。接收请求来源(包含协议和域名和端口部分)字符串，
        返回 ``True/False`` 。若设置了 ``check_origin`` ， ``allowed_origins`` 参数将被忽略
    :return: Flask视图函数
    """

    set_session_implement_for_target(target)

    if check_origin is None:
        check_origin = lambda origin: any(
            fnmatch.fnmatch(origin, patten)
            for patten in allowed_origins
        )

    view_func = partial(_webio_view, target=target,
                        session_expire_seconds=session_expire_seconds,
                        check_origin=check_origin)
    view_func.__name__ = 'webio_view'
    return view_func


def run_event_loop(debug=False):
    """运行事件循环

    基于协程的会话在启动Flask服务器之前需要启动一个单独的线程来运行事件循环。

    :param debug: Set the debug mode of the event loop.
       See also: https://docs.python.org/3/library/asyncio-dev.html#asyncio-debug-mode
    """
    global _event_loop
    _event_loop = asyncio.new_event_loop()
    _event_loop.set_debug(debug)
    asyncio.set_event_loop(_event_loop)
    _event_loop.run_forever()


def start_server(target, port=8080, host='localhost',
                 allowed_origins=None, check_origin=None,
                 disable_asyncio=False,
                 session_expire_seconds=DEFAULT_SESSION_EXPIRE_SECONDS,
                 debug=False, **flask_options):
    """启动一个 Flask server 来运行PyWebIO的 ``target`` 服务

    :param target: task function. It's a coroutine function is use CoroutineBasedSession or
        a simple function is use ThreadBasedSession.
    :param port: server bind port. set ``0`` to find a free port number to use
    :param host: server bind host. ``host`` may be either an IP address or hostname.  If it's a hostname,
    :param list allowed_origins: 除当前域名外，服务器还允许的请求的来源列表。
        来源包含协议和域名和端口部分，允许使用 ``*`` 作为通配符。 比如 ``https://*.example.com`` 、 ``*://*.example.com`` 、
    :param callable check_origin: 请求来源检查函数。接收请求来源(包含协议和域名和端口部分)字符串，
        返回 ``True/False`` 。若设置了 ``check_origin`` ， ``allowed_origins`` 参数将被忽略
    :param disable_asyncio: 禁用 asyncio 函数。仅在当 ``session_type=COROUTINE_BASED`` 时有效。
        在Flask backend中使用asyncio需要单独开启一个线程来运行事件循环，
        若程序中没有使用到asyncio中的异步函数，可以开启此选项来避免不必要的资源浪费
    :param session_expire_seconds: 会话过期时间。若 session_expire_seconds 秒内没有收到客户端的请求，则认为会话过期。
    :param debug: Flask debug mode
    :param flask_options: Additional keyword arguments passed to the constructor of ``flask.Flask.run``.
        ref: https://flask.palletsprojects.com/en/1.1.x/api/?highlight=flask%20run#flask.Flask.run
    """

    app = Flask(__name__)
    app.route('/io', methods=['GET', 'POST', 'OPTIONS'])(
        webio_view(target, session_expire_seconds,
                   allowed_origins=allowed_origins,
                   check_origin=check_origin)
    )

    @app.route('/')
    @app.route('/<path:static_file>')
    def serve_static_file(static_file='index.html'):
        return send_from_directory(STATIC_PATH, static_file)

    if not disable_asyncio and get_session_implement() is CoroutineBasedSession:
        threading.Thread(target=run_event_loop, daemon=True).start()

    app.run(host=host, port=port, debug=debug, **flask_options)
