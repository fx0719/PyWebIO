PyWebIO
==================

PyWebIO是一个用于在浏览器上获取输入和进行输出的工具库。能够将原有的通过终端交互的脚本快速服务化，供其他人在网络上通过浏览器访问使用；
PyWebIO还可以方便地整合进现有的Web服务，让你不需要编写Html和JS代码，就可以构建出具有良好可用性的Web程序。

特点：

- 使用同步而不是基于回调的方式获取输入，无需在各个步骤之间保存状态，使用更方便
- 代码侵入性小，对于旧脚本代码仅需修改输入输出逻辑
- 支持多用户与并发请求
- 支持整合到现有的Web服务，目前支持与Tornado和Flask的集成
- 同时支持基于线程的执行模型和基于协程的执行模型

Install
------------

.. code-block:: bash

    pip3 install pywebio

Quick start
------------

**Hello, world**

这是一个使用PywWebIO计算 `BMI指数 <https://en.wikipedia.org/wiki/Body_mass_index>`_ 的脚本:

.. code-block:: python

    from pywebio.input import input
    from pywebio.output import put_text

    def bmi():
        height = input("请输入你的身高(cm)：")
        weight = input("请输入你的体重(kg)：")

        BMI = float(weight) / (float(height) / 100) ** 2

        top_status = [(14.9, '极瘦'), (18.4, '偏瘦'),
                      (22.9, '正常'), (27.5, '过重'),
                      (40.0, '肥胖'), (float('inf'), '非常肥胖')]

        for top, status in top_status:
            if BMI <= top:
                put_text('你的 BMI 值: %.1f，身体状态：%s' % (BMI, status))
                break

    if __name__ == '__main__':
        bmi()

如果没有使用PywWebIO，这只是一个非常简单的脚本，而通过使用PywWebIO提供的输入输出函数，你可以在浏览器中与代码进行交互：

.. image:: /docs/assets/demo.gif

**向外提供服务**

上文对使用PyWebIO进行改造的程序，运行模式还是脚本，程序计算完毕后立刻退出。可以使用 `pywebio.start_server <https://pywebio.readthedocs.io/zh_CN/latest/server.html#pywebio.platform.start_server>`_ 将 ``bmi()`` 函数作为Web服务提供：

.. code-block:: python

    from pywebio import start_server
    from pywebio.input import input
    from pywebio.output import put_text

    def bmi():
        # same as above code

    if __name__ == '__main__':
        start_server(bmi)

**与现有Web框架整合**

仅需在现有的Tornado应用中加入加入两个 ``RequestHandler`` ，就可以将使用PyWebIO编写的函数整合进 ``Tornado`` 应用中

.. code-block:: python

    import tornado.ioloop
    import tornado.web
    from pywebio.platform.tornado import webio_handler
    from pywebio import STATIC_PATH

    class MainHandler(tornado.web.RequestHandler):
        def get(self):
            self.write("Hello, world")

    if __name__ == "__main__":
        application = tornado.web.Application([
            (r"/", MainHandler),
            (r"/bmi/io", webio_handler(bmi)),  # bmi 即为上文中使用`PyWebIO`进行改造的函数
            (r"/bmi/(.*)", tornado.web.StaticFileHandler, {"path": STATIC_PATH, 'default_filename': 'index.html'})
        ])
        application.listen(port=80, address='localhost')
        tornado.ioloop.IOLoop.current().start()

在 ``http://localhost/bmi/`` 页面上就可以计算BMI了

Document
------------

使用手册和开发文档见 `https://pywebio.readthedocs.io <https://pywebio.readthedocs.io>`_
