import more_itertools
import asyncio
import itertools

from .utils import convert_dict_to_bitrix_url
from .srh import BITRIX_MAX_BATCH_SIZE
from .server_response import ServerResponse

class MultipleServerRequestHandler:

    def __init__(self, srh, method, item_list, real_len=None, real_start=0):
        self.srh = srh
        self.method = method
        self.item_list = item_list
        self.real_len = real_len if real_len else len(item_list)
        self.real_start = real_start
        self.results = []

                
    async def run(self):
        self.prepare_batches()
        self.prepare_tasks()
        await self.get_results()
        return self.results


    def prepare_batches(self):
        batch_size = BITRIX_MAX_BATCH_SIZE

        while True:
            batches = [{
                'halt': 0,
                'cmd': {
                    self.batch_command_label(i, item): 
                    f'{self.method}?{convert_dict_to_bitrix_url(item)}'
                    for i, item in enumerate(next_batch)
                }}
                for next_batch in more_itertools.chunked(self.item_list, batch_size)
            ]
            
            URI_len_used = self.srh.URI_len_used('batch', batches[0])
            if URI_len_used < 1:
                break
            else:
                batch_size = int(batch_size // URI_len_used)

        self.method = 'batch'
        self.item_list = batches


    def batch_command_label(self, i, item):
        return f'cmd{i}'


    def prepare_tasks(self):
        for i in self.item_list:
            self.srh.add_request_task(self.method, i)


    async def get_results(self):
        self.pbar = self.srh.get_pbar(self.real_len, self.real_start)

        for task in self.srh.get_server_serponses():
            batch_response = await task
            unwrapped_result = ServerResponse(batch_response.result).result
            self.results.extend(self.extract_result_from_batch_response(unwrapped_result))
            self.pbar.update(len(unwrapped_result))

        self.pbar.close()


    def extract_result_from_batch_response(self, unwrapped_result):
        result_list = list(unwrapped_result.values())
        if type(result_list[0]) == list:
            result_list = list(itertools.chain(*result_list))
        return result_list


class MultipleServerRequestHandlerPreserveIDs(MultipleServerRequestHandler):

    def __init__(self, srh, method, item_list, ID_field):
        super().__init__(srh, method, item_list)
        self.ID_field = ID_field
        self.original_item_list = item_list.copy()
        

    async def run(self):
        await super().run()
        self.sort_results()
        return self.results
                        

    def batch_command_label(self, i, item):
        return item[self.ID_field]
    

    def extract_result_from_batch_response(self, unwrapped_result):
        result_list_of_tuples = unwrapped_result.items()
        return result_list_of_tuples


    def sort_results(self):
        # выделяем ID для облегчения дальнейшего поиска
        IDs_only = [i[self.ID_field] for i in self.original_item_list]
            
        # сортируем results на базе порядка ID в original_item_list
        self.results.sort(key = lambda item: 
            IDs_only.index(item[0]))