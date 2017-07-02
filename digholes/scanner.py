from redisqueue.scheduler import PipeScheduler
from concurrent.futures import ThreadPoolExecutor
import nmap
import socket
from functools import partial
import logging

import importlib
import six
import time
from redisqueue import connection

class NmapScanner(PipeScheduler):

    """从redis-queue中获取ip，进行nmapscan，然后再存放到另一个redis-queue中"""

    def nmap_scan(self, target_hosts):
        """
        主要用来工作区域
        获取当前的ip地址加入Nmap扫描中
        如果发现地址存活，就输出服务等信息
        """
        try:
            scanner = nmap.PortScanner()
            #  scanner.scan(target_hosts,arguments='-Pn -sT -sV --allports --version-trace')
            scanner.scan(target_hosts,arguments='')
            result = []
            for target_host in scanner.all_hosts():
                if scanner[target_host].state() == 'up' and scanner[target_host]['tcp']:
                    for target_port in scanner[target_host]['tcp']:
                        if scanner[target_host]['tcp'][int(target_port)]['state'] == 'open':
                            result.append(f"http://{target_host}:{target_port}")
                else:
                    break
                    continue
            return result
        except Exception as e:
            self.logger.info(target_hosts+'\t'+str(e))
            return []

    def scan_single(self, _):
        while True:
            ip = self.dequeue('in')
            if ip:
                result = self.nmap_scan(ip)
                for url in result:
                    self.enqueue(url, 'out')

    def scan_bulk(self, n=5):
        with ThreadPoolExecutor(n) as pool:
            pool.map(self.scan_single, range(n))

class SocketScanner(PipeScheduler):

    """通过socket进行端口扫描的类"""
    def __init__(self, server,
                 persist=False,
                 flush_on_start=False,
                 queue_in_key='queue_in:%(timestamp)s' % {'timestamp': int(time.time())},
                 queue_in_cls='redisqueue.rqueues.FifoQueue',
                 queue_out_key='queue_out:%(timestamp)s' % {'timestamp': int(time.time())},
                 queue_out_cls='redisqueue.rqueues.FifoQueue',
                 idle_before_close=0,
                 serializer=None,
                 num_scan_host_threads=5,
                 num_scan_port_threads=1000):

        """Initialize scheduler.

        Parameters
        ----------
        num_scan_host_threads: int
            同时扫描host的线程数
        num_scan_port_threads: int
            同时扫描port的线程数
        """
        super().__init__(server, persist, flush_on_start, queue_in_key, queue_in_cls, queue_out_key, queue_out_cls, idle_before_close, serializer)
        self.num_scan_host_threads = num_scan_host_threads
        self.num_scan_port_threads = num_scan_port_threads

    @classmethod
    def from_settings(cls, settings):
        kwargs = {
            'persist': settings.get('SCHEDULER_PERSIST', True),
            'flush_on_start': settings.get('SCHEDULER_FLUSH_ON_START', False),
            'queue_in_key': settings.get('SCHEDULER_QUEUE_IN_KEY', 'queue_in:%(timestamp)s' % {'timestamp': int(time.time())}),
            'queue_in_cls': settings.get('SCHEDULER_QUEUE_IN_CLASS', 'redisqueue.rqueues.FifoQueue'),
            'queue_out_key': settings.get('SCHEDULER_QUEUE_OUT_KEY', 'queue_out:%(timestamp)s' % {'timestamp': int(time.time())}),
            'queue_out_cls': settings.get('SCHEDULER_QUEUE_OUT_CLASS', 'redisqueue.rqueues.FifoQueue'),
            'idle_before_close': settings.get('SCHEDULER_IDLE_BEFORE_CLOSE', 0),
            'serializer': settings.get('SCHEDULER_SERIALIZER', None),
            'num_scan_host_threads': settings.get('NUM_SCAN_HOST_THREADS', 5),
            'num_scan_port_threads': settings.get('NUM_SCAN_PORT_THREADS', 1000),
        }

        # Support serializer as a path to a module.
        if isinstance(kwargs.get('serializer'), six.string_types):
            kwargs['serializer'] = importlib.import_module(kwargs['serializer'])

        server = connection.from_settings(settings)
        # Ensure the connection is working.
        server.ping()

        return cls(server=server, **kwargs)

    def socket_scan(self, target_host, target_port):
        s = socket.socket()
        s.settimeout(0.1)
        if s.connect_ex((target_host, target_port)) == 0:
            url = f'http://{target_host}:{target_port}'
        else:
            url = ''
        s.close()
        return url

    def scan_single(self, _):
        while True:
            ip = self.dequeue('in')
            if ip:
                self.logger.info(f"scan:{ip}")
                partial_scan = partial(self.socket_scan, ip)
                with ThreadPoolExecutor(self.num_scan_port_threads) as pool:
                    result = pool.map(partial_scan, range(1, 65536))
                    for url in result:
                        if url:
                            self.logger.info(f"produce:{url}")
                            self.enqueue(url, 'out')

    def scan_bulk(self, n=0):
        n = self.num_scan_host_threads if not n else n
        with ThreadPoolExecutor(n) as pool:
            pool.map(self.scan_single, range(n))


def main(settings):
    """

    :settigs: TODO
    :returns: TODO

    """
    logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(name)s[line:%(lineno)d] %(levelname)s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S')
    #  n = NmapScanner.from_settings(settings)
    #  n.open()
    #  n.scan_single(1)
    #  n.scan_bulk(5)
    s = SocketScanner.from_settings(settings)
    s.open()
    #  s.scan_single(1)
    s.scan_bulk()
    s.close()


if __name__ == "__main__":
    from multiprocessing import Process
    settings = {'REDIS_HOST': '127.0.0.1',
                'REDIS_PORT': 6888,
                'SCHEDULER_SERIALIZER': 'json',
                'SCHEDULER_QUEUE_IN_KEY': 'digholes:queue_ip_pool',
                'SCHEDULER_QUEUE_IN_CLASS': 'redisqueue.rqueues.LifoQueue',
                'SCHEDULER_QUEUE_OUT_KEY' : 'digholes:queue_url_pool'
            }
    p = Process(target=main, args=(settings,))
    p.start()
    p.join()
