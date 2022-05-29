# fast_bitrix24
API wrapper для Питона для быстрого получения данных от Битрикс24 через REST API.

[![Статистика загрузок](https://img.shields.io/pypi/dm/fast-bitrix24.svg)](https://pypistats.org/packages/fast-bitrix24)
![Статистика тестов](https://github.com/leshchenko1979/fast_bitrix24/workflows/tests/badge.svg)
[![codecov](https://codecov.io/gh/leshchenko1979/fast_bitrix24/branch/master/graph/badge.svg?token=UEQ3KITRSX)](https://codecov.io/gh/leshchenko1979/fast_bitrix24)
[![Sourcery](https://img.shields.io/badge/Sourcery-enabled-brightgreen)](https://sourcery.ai)
[![CodeFactor](https://www.codefactor.io/repository/github/leshchenko1979/fast_bitrix24/badge)](https://www.codefactor.io/repository/github/leshchenko1979/fast_bitrix24)

- [Основная функциональность](#Основная-функциональность)
- [Начало работы](#Начало-работы)
- [Примеры использования](#Примеры-использования)
- [Как это работает](#Как-это-работает)
- [Советы и подсказки](#Советы-и-подсказки)
- [Как связаться с автором](#Как-связаться-с-автором)
- [Подробный справочник по API](API.md)

## Основная функциональность

### Высокая скорость обмена данными

![Тест скорости](speed_test.gif)

- На больших списках скорость обмена данными с сервером достигает тысяч элементов в секунду.
- Автоматическая упаковка запросов в батчи сокращает количество требуемых запросов к серверу и ускоряет обмен данными.
- Батчи отправляются на сервер не последовательно, а параллельно.
- Продвинутые стратегии работы с постраничным доступом ускоряют выгрузку на порядки (см. [результаты тестов](https://github.com/leshchenko1979/fast_bitrix24/discussions/113)).

### Избежание отказов сервера
- Автоматический autothrottling - если сервер возвращает ошибки, скорость автоматически понижается.
- Если сервер для сложных запросов начинает возвращать ошибки, можно в одну строку понизить скорость запроосов.

### Удобство кода
- Высокоуровневые списочные методы для сокращения количества необходимого кода. Большинство операций занимают только одну строку кода. Обработка параллельных запросов, упаковка запросов в батчи и многое другое убрано "под капот".
- Позволяет задавать параметры запроса именно в таком виде, как они приведены в [документации к Bitrix24 REST API](https://dev.1c-bitrix.ru/rest_help/index.php). Параметры проверяются на корректность для облегчения отладки.
- Выполнение запросов автоматически сопровождается прогресс-баром из пакета `tqdm`, иллюстрирующим не только количество обработанных элементов, но и прошедшее и оставшееся время выполнения запроса.

### Синхронный и асинхронный клиенты
- Наличие асинхронного клиента позволяет использовать библиотеку для написания веб-приложений (например, телеграм-ботов).

### Нас используют
- [Яндекс](https://github.com/leshchenko1979/fast_bitrix24/issues/159#issuecomment-1104539717)

## Начало работы
Установите модуль через `pip`:
```shell
pip install fast-bitrix24
```

Далее в python:

```python
from fast_bitrix24 import Bitrix

# замените на ваш вебхук для доступа к Bitrix24
webhook = "https://your_domain.bitrix24.ru/rest/1/your_code/"
b = Bitrix(webhook)
```

Методы полученного объекта `b` в дальнейшем используются для взаимодействия с сервером Битрикс24.

## Примеры использования

### `get_all()`

Чтобы получить полностью список сущностей, используйте метод `get_all()`:

```python
# список лидов
leads = b.get_all('crm.lead.list')
```

Метод `get_all()` возвращает список, где каждый элемент списка является словарем, описывающим одну сущность из запрошенного списка.

Вы также можете использовать параметр `params`, чтобы кастомизировать запрос:

```python
# список сделок в работе, включая пользовательские поля
deals = b.get_all(
    'crm.deal.list',
    params={
        'select': ['*', 'UF_*'],
        'filter': {'CLOSED': 'N'}
})
```

Если у вас есть необходимость быстро выгрузить большие объемы информации (значения всех полей в длинных списках - в 20+ тыс. элементов), то используйте метод `list_and_get()` (см. [документацию по методу](https://github.com/leshchenko1979/fast_bitrix24#метод-list_and_getself-method_branch-str---dict)).

### `get_by_ID()`
Если у вас есть список ID сущностей, то вы можете получить их свойства при помощи метода `get_by_ID()`
и использовании методов вида `*.get`:

```python
'''
получим список всех контактов, привязанных к сделкам, в виде
{
    ID_сделки_1: [контакт_1, контакт_2, ...],
    ID_сделки_2: [контакт_1, контакт_2, ...],
    ...
}
'''

contacts = b.get_by_ID(
    'crm.deal.contact.items.get',
    [d['ID'] for d in deals])
```
Метод `get_by_ID()` возвращает словарь с элементами вида `ID: result`, где `result` - ответ сервера относительно этого `ID`.

### `call()`
Чтобы создавать, изменять или удалять список сущностей, используйте метод `call()`:

```python
# вставим в начало названия всех сделок их ID
tasks = [
    {
        'ID': d['ID'],
        'fields': {
            'TITLE': f'{d["ID"]} - {d["TITLE"]}'
        }
    }
    for d in deals
]

b.call('crm.deal.update', tasks)
```
Метод `call()` возвращает список ответов сервера по каждому элементу переданного списка.

### call(raw=True)
Рекомендуется использовать метод `call(raw=True)` в следующих случаях:
- для вызова методов типа `crm.lead.fields` или `crm.deal.fields`, которые не требуют дополнительных параметров и возвращают словарь (`dict`), а не список (`list`),
- для отправки запросов, в параметрах которых есть `None` (для стирания значения полей).

```python
# вернуть dict с полями лида
b.call('crm.lead.fields', raw=True)

# стереть DESCRIPTION в лиде 123
params = {"ID": 123, "fields": {"DESCRIPTION": None}}
b.call('crm.lead.update', params, raw=True)
```

### `call_batch()`
Если вы хотите вызвать пакетный метод, используйте `call_batch()`:

```python
results = b.call_batch ({
    'halt': 0,
    'cmd': {
        'deals': 'crm.deal.list', # берем список сделок
        # и берем список дел по первой из них
        'activities': 'crm.activity.list?filter[ENTITY_TYPE]=3&filter[ENTITY_ID]=$result[deals][0][ID]'
    }
})

```
### Асинхронные вызовы
Если требуется использование бибилиотеки в асинхронном коде, то вместо клиента `Bitrix()` создавайте клиент класса `BitrixAsync()`:
```python
from fast_bitrix24 import BitrixAsync
b = BitrixAsync(webhook)
```
Все методы у него - синхронные аналоги методов из `Bitrix()`, описанных выше:
```python
leads = await b.get_all('crm.lead.list')
```
## Как это работает
1. Перед обращением к серверу во всех методах класса `Bitrix` происходит проверка корректности самых популярных параметров, передаваемых к серверу, и поднимаются исключения `TypeError` и `ValueError` при наличии ошибок.
2. Cоздаются запросы на получение всех элементов из запрошенного списка.
3. Созданные запросы упаковываются в батчи по 50 запросов в каждом.
4. Полученные батчи параллельно отправляются на сервер с регулировкой скорости запросов (см. ниже "Как fast_bitrix24 регулирует скорость запросов").
5. Ответы (содержимое поля `result`) собираются в единый плоский список и возвращаются пользователю.
    - Поднимаются исключения класса `aiohttp.ClientError`, если сервер Битрикс вернул HTTP-ошибку, и `RuntimeError`, если код ответа был `200`, но ошибка сдержалась в теле ответа сервера.
    - Происходит сортировка ответов (кроме метода `get_all()`) - порядок элементов в списке результатов совпадает с порядком соответствующих запросов в списке запросов.

В случае с методом `get_all()` пункт 2 выше выглядит немного сложнее:
  - `get_all()` делает первый запрос к серверу Битрикс24 с указанным методом и параметрами.
  - Сервер возвращает первую страницу (50 элементов) и параметр `total` - общее количество элементов, найденных по запросу.
  - Исходя из полученного общего количества элементов, создаются запросы на каждую из страниц (всего `total // 50 - 1` запросов), необходимых для получения всех запрошенных элементов.

В связи с тем, что выполнение `get_all()` по длинным спискам может занимать долгое время, в течение которого пользователи могут добавлять новые элементы в список, может возникнуть ситуация, когда общее полученное количество элементов может не соответствовать изначальному значению `total`. В таких случаях будет выдано стандартное питоновское предупреждение (`warning`).

### Как `fast_bitrix24` регулирует скорость запросов
По умолчанию `fast_bitrix24` игнорирует официальные ограничения Битрикс24 по скорости запросов (см. ниже "Официальная политика Битрикс24 по скорости запросов") и вместо этого начинает снижать скорость запросов, если сервер начинает возвращать ошибки (autothrottling). Подобный подход позволяет на порядки увеличить скорость получения данных (см. [тесты скорости](https://github.com/leshchenko1979/fast_bitrix24/blob/master/speed_tests/strategies.ipynb)).

Чтобы соблюдать официальные правила, при создании экземпляра класса `Bitrix` укажите параметр `respect_velocity_policy=True`.
### Официальная политика Битрикс24 по скорости запросов
1. Существует пул из 50 запросов, которые можно направить без ожидания.
2. Пул пополняется со скоростью 2 запроса в секунду.
3. При исчерпании пула и несоблюдении режима ожидания сервер возвращает ошибку.

## Советы и подсказки
### А умеет ли ваша библиотека ...?
Посмотрите в [справочник по API](API.md). Если не нашли ответа, [свяжитесь с автором](#как-связаться-с-автором).

### А как мне сформировать запрос к Битриксу, чтобы  ...?

1. Поищите в [официальной документации по REST API](https://dev.1c-bitrix.ru/rest_help/).
1. Если на ваш вопрос там нет ответа - попробуйте задать его в [группе "Партнерский REST API" в Сообществе разработчиков Битрикс24](https://dev.bitrix24.ru/workgroups/group/34/).
1. Спросите в Телеграме в [группе разработчиков Битрикс24](https://t.me/bit24dev).
1. Спросите в Телеграме в [группе пользователей fast_bitrix24](https://t.me/fast_bitrix24).
1. Спросите на [русском StackOverflow](https://ru.stackoverflow.com/questions/tagged/битрикс24).

### А как понять, что отправляется на сервер и что он возвращает?
Включите логирование запросов и ответов сервера.
```python
import logging

logging.getLogger('fast_bitrix24').addHandler(logging.StreamHandler())
```

### Я хочу добавить несколько лидов списком, но получаю ошибку сервера.
Оберните вызов `call()` в `slow`:

```python
with b.slow():
    results = b.call('crm.lead.add', tasks)
```

[См. подробнее](API.md#контекстный-менеджер-slowmaxconcurrentrequests-int--1) о `slow`.

### Я хочу вызвать `call()` только один раз, а не по списку.
Передавайте параметры запроса методу `call()`, он может делать как запросы по списку, так и единичный запрос:

```python
method = 'crm.lead.add'
params = {'fields': {'TITLE': 'Чпок'}}
b.call(method, params)
```
Результатом будет ответ сервера по этому одному элементу.

Однако, если такие вызовы делаются несколько раз, то более эффективно формировать из них список и вызывать `call()` единожды по всему списку.

### Как сортируются результаты при вызове `get_all()`?
Пока что никак.

Все обращения к серверу происходят асинхронно и список результатов отсортирован в том порядке, в котором сервер возвращал ответы. Если вам требуется сортировка, то вам нужно делать ее самостоятельно, например:

```python
deals = b.get_all('crm.deal.list')
deals.sort(key = lambda d: int(d['ID']))
```

### Я использую `get_all()` для получения всех полей всех элементов списка, но это происходит слишком долго. Как ускорить этот процесс?
Попробуйте применить метод `list_and_get()` - он стабильно показывает на порядок лучшие результаты на больших объемах информации.

См. [результаты тестов](https://github.com/leshchenko1979/fast_bitrix24/discussions/113).

### Я получаю ошибку сертификата SSL. Что делать?
Если вы получаете `SSLCertVerificationError` / `CERTIFICATE_VERIFY_FAILED`, попробуйте отключить верификацию сертификата SSL. При инициализации передайте в `BitrixAsync` параметр `client`,  где будет инициализированный вами экземпляр `aiohttp.ClientSession`, у которого будет отключена верификация SSL:
```python
import aiohttp
import asyncio

from fast_bitrix24 import BitrixAsync


async def main():
    # Инициализировать HTTP-клиента без верификации SSL и передать его в `BitrixAsync`
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as client:
        b = BitrixAsync(webhook, client=client)

        # Далее ваши вызовы Битрикса
        ...


asyncio.run(main())
```

## Как связаться с автором
- telegram: https://t.me/fast_bitrix24
- создать новый github issue: https://github.com/leshchenko1979/fast_bitrix24/issues/new
