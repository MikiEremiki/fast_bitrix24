'''Высокоуровневый API для доступа к Битрикс24'''

import urllib.parse
import asyncio
import aiohttp
import time
import itertools
import more_itertools
import pickle
import warnings
from collections.abc import Sequence, Iterable

from tqdm import tqdm

from .correct_asyncio import *
from .utils import _correct_webhook, _bitrix_url, _merge_dict

BITRIX_URI_MAX_LEN = 5820
BITRIX_MAX_BATCH_SIZE = 50
BITRIX_POOL_SIZE = 50
BITRIX_RPS = 2.0

##########################################
#
#   BitrixSemaphoreWrapper class
#
##########################################


class BitrixSemaphoreWrapper():
    '''
    Используется для контроля скорости доступа к серверам Битрикс.

    Основная цель - вести учет количества запросов, которые можно передать
    серверу Битрикс без получения ошибки `503`.

    Используется как контекстный менеджер, оборачивающий несколько
    последовательных запросов к серверу.

    Чтобы все работало, нужно, чтобы внутри метода класса `Bitrix`, в котором
    используется этот семафор, выполнял параллельно по совими задачами и
    корутину-метод `release_sem()`.
        
    Параметры:
    - pool_size: int - размер пула доступных запросов.
    - requests_per_second: int - скорость подачи запросов.

    Методы:
    - acquire(self)
    - release_sem(self)
    '''


    def __init__(self, pool_size: int, requests_per_second: float):
        self._stopped_time = None
        self._stopped_value = None
        self.requests_per_second = requests_per_second
        self._pool_size = pool_size

    async def __aenter__(self):
        global _SLOW
        self._sem = asyncio.BoundedSemaphore(self._pool_size)
        if _SLOW:
            self._slow_lock = asyncio.Lock()
        else:
            if self._stopped_time:
                '''
-----v-----------------------------v---------------------
     ^ - _stopped_time             ^ - current time
     |-------- time_passed --------|
     |- step -|- step -|- step |          - add_steps (whole steps to add)
                               |- step -| - additional 1 step added
                                   |-aw-| - additional waiting time
                '''
                time_passed = time.monotonic() - self._stopped_time

                # сколько шагов должно было пройти
                add_steps = time_passed / self.requests_per_second // 1

                # сколько шагов могло пройти с учетом ограничений + еще один
                real_add_steps = min(self._pool_size - self._stopped_value,
                                    add_steps + 1)

                # добавляем пропущенные шаги
                self._sem._value += real_add_steps

                # ждем время, излишне списанное при добавлении дополнительного шага
                await asyncio.sleep((add_steps + 1) / self.requests_per_second - time_passed)

                self._stopped_time = None
                self._stopped_value = None


    async def __aexit__(self, a1, a2, a3):
        self._stopped_time = time.monotonic()
        
        if _SLOW:
            # в slow-режиме обнуляем пул запросов, чтобы после выхода
            # не выдать на сервер пачку запросов и не словить отказ
            self._stopped_value = 0
        else:
            self._stopped_value = self._sem._value


    async def release_sem(self):
        '''
        Корутина-метод, которая увеличивает счетчик доступных в пуле запросов.

        Должна запускаться единожды в параллели со всеми другими задачами
        внутри основного цикла `Bitrix._request_list`, кроме случаев
        выполнения в slow-режиме, когда она запускаться на должна.
        '''

        while True:
            if self._sem._value < self._sem._bound_value:
                self._sem.release()
            await asyncio.sleep(1 / self.requests_per_second)


    async def acquire(self):
        '''
        Вызов `await acquire()` должен предшествовать любому обращению
        к серверу Битрикс. Он возвращает `True`, когда к серверу
        можно осуществить запрос.

        Использование:
        ```
        await self.aquire()
        # теперь можно делать запросы
        ...
        ```
        '''
        global _SLOW, _SLOW_RPS
        if _SLOW:
            # ждать, пока отработают другие запросы, запущенные параллельно,
            async with self._slow_lock:
            # потом ждать основное время "остывания"
                await asyncio.sleep(1 / _SLOW_RPS)
            return True 
        else:
            return await self._sem.acquire()


##########################################
#
#   Bitrix class
#
##########################################


class Bitrix:
    '''
    Класс, оборачивающий весь цикл запросов к серверу Битрикс24.

    Параметры:
    - webhook: str - URL вебхука, полученного от сервера Битрикс
    - verbose: bool = True - показывать ли прогрессбар при выполнении запроса

    Методы:
    - get_all(self, method: str, params: dict = None) -> list
    - get_by_ID(self, method: str, ID_list: Sequence, ID_field_name: str = 'ID', params: dict = None) -> list
    - call(self, method: str, item_list: Sequence) -> list
    '''

    def __init__(self, webhook: str, verbose: bool = True):
        '''
        Создает объект класса Bitrix.

        '''
        
        self.webhook = webhook
        self._correct_webhook()
        self._sw = BitrixSemaphoreWrapper(BITRIX_POOL_SIZE, BITRIX_RPS)
        self._autobatch = True
        self._verbose = verbose


    def _correct_webhook(self):

        def _url_valid(url):
            try:
                result = urllib.parse.urlparse(url)
                return all([result.scheme, result.netloc, result.path])
            except:
                return False

        if not isinstance(self.webhook, str):
            raise TypeError(f'Webhook should be a {str}')

        if not _url_valid(self.webhook):
            raise ValueError('Webhook is not a valid URL')

        if self.webhook[-1] != '/':
            self.webhook += '/'

    async def _request(self, session, method, params=None, pbar=None):
        await self._sw.acquire()
        url = f'{self.webhook}{method}?{_bitrix_url(params)}'
        async with session.get(url) as response:
            r = await response.json(encoding='utf-8')
        if 'result_error' in r.keys():
            raise RuntimeError(f'The server reply contained an error: {r["result_error"]}')
        if pbar:
            pbar.update(len(r['result']))
        return r['result'], (r['total'] if 'total' in r.keys() else None)


    async def _request_list(self, method, item_list, real_len=None, real_start=0, preserve_IDs=False):
        original_item_list = item_list.copy()
        
        if not real_len:
            real_len = len(item_list)

        # подготовить батчи
        if (self._autobatch) and (method != 'batch'):

            batch_size = BITRIX_MAX_BATCH_SIZE
            while True:
                batches = [{
                    'halt': 0,
                    'cmd': {
                        item[preserve_IDs] if preserve_IDs else f'cmd{i}': 
                        f'{method}?{_bitrix_url(item)}'
                        for i, item in enumerate(next_batch)
                    }}
                    for next_batch in more_itertools.chunked(item_list, batch_size)
                ]
                
                # проверяем длину получившегося URI
                uri_len = len(self.webhook + 'batch' +
                              _bitrix_url(batches[0]))
                
                # и если слишком длинный, то уменьшаем размер батча
                # и уходим на перекомпоновку
                if uri_len > BITRIX_URI_MAX_LEN:
                    batch_size = int(
                        batch_size // (uri_len / BITRIX_URI_MAX_LEN))
                else:
                    break

            method = 'batch'
            item_list = batches

        # основная часть - отправляем запросы
        async with self._sw, aiohttp.ClientSession(raise_for_status=True) as session:
            global _SLOW
            tasks = [asyncio.create_task(self._request(session, method, i))
                        for i in item_list]
            if not _SLOW:
                tasks.append(asyncio.create_task(self._sw.release_sem()))

            if self._verbose:
                pbar = tqdm(total=real_len, initial=real_start)
            
            results = []
            tasks_to_process = len(item_list)
            for x in asyncio.as_completed(tasks):
                r, __ = await x
                if r['result_error']:
                    raise RuntimeError(f'The server reply contained an error: {r["result_error"]}')
                if method == 'batch':
                    if preserve_IDs:
                        r = r['result'].items()
                    else:
                        r = list(r['result'].values())
                        if type(r[0]) == list:
                            r = list(itertools.chain(*r))
                results.extend(r)
                if self._verbose:
                    pbar.update(len(r))
                tasks_to_process -= 1
                if tasks_to_process == 0:
                    break
            if self._verbose:
                pbar.close()
            
            # сортировка результатов в том же порядке, что и в original_item_list
            if preserve_IDs:
                
                # выделяем ID для облегчения дальнейшего поиска
                IDs_only = [i[preserve_IDs] for i in original_item_list]
                    
                # сортируем results на базе порядка ID в original_item_list
                results.sort(key = lambda item: 
                    IDs_only.index(item[0]))
            
            return results


    def get_all(self, method: str, params: dict = None) -> list:
        '''
        Получить полный список сущностей по запросу method.

        Под капотом использует параллельные запросы и автоматическое построение
        батчей, чтобы ускорить получение данных. Также самостоятельно
        обратывает постраничные ответы сервера, чтобы вернуть полный список.

        Параметры:
        - method - метод REST API для запроса к серверу
        - params - параметры для передачи методу. Используется именно тот формат,
                который указан в документации к REST API Битрикс24. get_all() не
                поддерживает параметры 'start', 'limit' и 'order'.

        Возвращает полный список сущностей, имеющихся на сервере,
        согласно заданным методу и параметрам.
        '''

        return GetAllUserRequest(self, method, params).run()

    def get_by_ID(self, method: str, ID_list: Sequence, ID_field_name: str = 'ID',
        params: dict = None) -> list:
        '''
        Получить список сущностей по запросу method и списку ID.

        Используется для случаев, когда нужны не все сущности,
        имеющиеся в базе, а конкретный список поименованных ID.
        Например, все контакты, привязанные к сделкам.

        Параметры:
        - method - метод REST API для запроса к серверу
        - ID_list - список ID
        - ID_list_name - название поля, которе будет подаваться в запрос для 
            каждого элемента ID_list
        - params - параметры для передачи методу. Используется именно тот
            формат, который указан в документации к REST API Битрикс24

        Возвращает список кортежей вида:

            [
                (ID, <результат запроса>), 
                (ID, <результат запроса>), 
                ...
            ]

        Вторым элементом каждого кортежа будет результат выполнения запроса
        относительно этого ID. Это может быть, например, список связанных
        сущностей или пустой список, если не найдено ни одной привязанной
        сущности.
        '''

        return GetByIDUserRequest(self, method, params, ID_list, ID_field_name).run()

    def call(self, method: str, item_list: Sequence) -> list:
        '''
        Вызвать метод REST API по списку.

        Параметры:
        - method - метод REST API
        - item_list - список параметров вызываемого метода

        Возвращает список ответов сервера для каждого из элементов item_list.
        '''

        return CallUserRequest(self, method, item_list).run()


class UserRequestAbstract():
    def __init__(self, bitrix: Bitrix, method: str, params: dict):
        self.bitrix = bitrix
        self.method = method
        self.params = params
        
    def check_args(self):
        if self.params:
            self.check_params(self.params)
        self.check_special_limitations()

    def check_params(self, p):
        # check if p is dict
        if not isinstance(p, dict):
            raise TypeError('params agrument should be a dict')

        clauses = {
            'select': list,
            'halt': int,
            'cmd': dict,
            'limit': int,
            'order': dict,
            'filter': dict,
            'start': int,
            'fields': dict
        }

        # check for allowed types of key values
        for pi in p.items():
            if pi[0] in clauses.keys():
                t = clauses[pi[0].lower()]
                if t and not (
                    (isinstance(pi[1], t)) or
                    ((t == list) and (any([isinstance(pi[1], x) for x in [list, tuple, set]])))
                ):
                    raise TypeError(f'Clause "{pi[0]}" should be of type {t}, '
                        'but its type is {type(pi[1])}')

    
    def check_special_limitations(self):
        raise NotImplementedError
    
    
class GetAllUserRequest(UserRequestAbstract):
    def run(self):
        self.check_args()
        return asyncio.run(self.get_paginated_list())


    def check_special_limitations(self):
        if self.params:
            for k in self.params.keys():
                if k.lower() in ['start', 'limit', 'order']:
                    raise ValueError("get_all() doesn't support parameters 'start', 'limit' or 'order'")

    
    async def get_paginated_list(self):
        self.add_order_parameter()

        await self.make_first_request()
        if self.no_more_results_expected():
            return self.results

        await self.make_remaining_requests()

        self.dedup_results()
                
        return self.results


    def add_order_parameter(self):
        # необходимо установить порядок сортировки, иначе сортировка будет рандомная
        # и сущности будут повторяться на разных страницах
        
        if self.params:
            if 'order' not in [x.lower() for x in self.params.keys()]:
                self.params.update({'order': {'ID': 'ASC'}})
        else:
            self.params = {'order': {'ID': 'ASC'}}

    
    async def make_first_request(self):
        async with self.bitrix._sw, aiohttp.ClientSession(raise_for_status=True) as session:
            self.results, self.total = await self.bitrix._request(session, self.method, self.params)


    def no_more_results_expected(self):
        return not self.total or self.total <= 50 or self.total == len(self.results)


    async def make_remaining_requests(self):
        self.results.extend(
            await self.bitrix._request_list(
                method = self.method, 
                item_list = [
                    _merge_dict({'start': start}, self.params)
                    for start in range(len(self.results), self.total, 50)
                ], 
                real_len = self.total, 
                real_start = len(self.results)
            )
        )


    def dedup_results(self):
        # дедупликация через сериализацию, превращение в set и десериализацию
        self.results = [pickle.loads(y) for y in set([pickle.dumps(x) for x in self.results])] \
            if self.results else []

        if len(self.results) != self.total:
            warnings.warn(f"Number of results returned ({len(self.results)}) "
                f"doesn't equal 'total' from the server reply ({self.total})",
                RuntimeWarning)


class GetByIDUserRequest(UserRequestAbstract):
    def __init__(self, bitrix: Bitrix, method: str, params: dict, ID_list, ID_field_name):
        super().__init__(bitrix, method, params)
        self.ID_list = ID_list
        self.ID_field_name = ID_field_name
        
        
    def check_special_limitations(self):
        if self.params: 
            for k in self.params.keys():
                if k.lower() == 'id':
                    raise ValueError("get_by_ID() doesn't support parameter 'ID' within the 'params' argument")

        if not(isinstance(self.ID_list, Sequence)):
            raise TypeError("get_by_ID(): 'ID_list' should be a sequence")


    def run(self):
        self.check_args()

        if self.list_empty():
            return []
        
        self.prepare_item_list()
        
        return asyncio.run(self.bitrix._request_list(
            self.method,
            self.item_list,
            preserve_IDs=self.ID_field_name
        ))

        
    def list_empty(self):
        return len(self.ID_list) == 0
    
    
    def prepare_item_list(self):
        if self.params:
            self.item_list = [
                _merge_dict({self.ID_field_name: ID}, self.params) 
                for ID in self.ID_list
            ]
        else:
            self.item_list = [
                {self.ID_field_name: ID} 
                for ID in self.ID_list
            ] 


class CallUserRequest(GetByIDUserRequest):
    def __init__(self, bitrix: Bitrix, method: str, item_list):
        self.bitrix = bitrix
        self.method = method
        self.item_list = item_list
        self.params = None
        self.ID_field_name = '__order'

        
    def check_special_limitations(self):
        if not isinstance(self.item_list, Sequence):
            raise TypeError("call(): 'item_list' should be a sequence")

        try:
            [self.check_params(p) for p in self.item_list]
        except (TypeError, ValueError) as err:
            raise ValueError(
                'item_list contains items with incorrect method params') from err 


    def run(self):
        results = super().run()
        
        # убираем поле с порядковым номером из результатов
        return [item[1] for item in results]


    def list_empty(self):
        return len(self.item_list) == 0

    
    def prepare_item_list(self):
        # добавим порядковый номер
        self.item_list = [
            _merge_dict(item, {self.ID_field_name: 'order' + str(i)}) 
            for i, item in enumerate(self.item_list)
        ]
        
        
##########################################
#
#   slow() context manager
#
##########################################

_SLOW = False
_SLOW_RPS = 0

class slow:
    def __init__(self, requests_per_second = 0.5):
        global _SLOW_RPS
        _SLOW_RPS = requests_per_second

    def __enter__(self):
        global _SLOW
        _SLOW = True

    def __exit__(self, a1, a2, a3):
        global _SLOW, _SLOW_RPS
        _SLOW = False
        _SLOW_RPS = 0
