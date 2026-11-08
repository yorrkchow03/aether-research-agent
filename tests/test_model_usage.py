"""Reported usage compatibility: unknown/malformed counts are never free calls."""
from types import SimpleNamespace
import unittest

from research_agent.model_usage import response_usage


def message(raw,usage=None):
    return SimpleNamespace(usage_metadata=usage,response_metadata={'token_usage':raw})


class ModelUsageTests(unittest.TestCase):
    def test_compatible_ark_counts_and_cache_are_preserved_without_discount(self):
        raw={'prompt_tokens':1544,'completion_tokens':138,'total_tokens':1682,
            'prompt_tokens_details':{'cached_tokens':1336}}
        result=response_usage(message(raw))
        self.assertEqual(result,{'input_tokens':1544,'output_tokens':138,'total_tokens':1682,
            'input_token_details':{'cache_read':1336}})
        self.assertEqual(raw['prompt_tokens'],1544)

    def test_native_usage_metadata_takes_precedence_and_legitimate_zero_is_reported(self):
        usage={'input_tokens':8,'output_tokens':2,'total_tokens':10}
        self.assertEqual(response_usage(message({'prompt_tokens':99,'completion_tokens':99},usage)),usage)
        self.assertEqual(response_usage(message({'prompt_tokens':0,'completion_tokens':0})),
            {'input_tokens':0,'output_tokens':0,'total_tokens':0})

    def test_absent_partial_and_invalid_count_shapes_remain_unknown(self):
        for raw in (None,{},[], 'bad', {'prompt_tokens':5},
                    {'prompt_tokens':True,'completion_tokens':2},
                    {'prompt_tokens':-1,'completion_tokens':2},
                    {'prompt_tokens':5,'completion_tokens':False},
                    {'prompt_tokens':'5','completion_tokens':2},
                    {'prompt_tokens':5.5,'completion_tokens':2}):
            with self.subTest(raw=raw):self.assertIsNone(response_usage(message(raw)))
        self.assertIsNone(response_usage(None))

    def test_malformed_cache_details_cannot_break_research_or_fabricate_cached_counts(self):
        for details in ('bad',[],True,{'cached_tokens':-1},{'cached_tokens':True}):
            with self.subTest(details=details):
                result=response_usage(message({'prompt_tokens':7,'completion_tokens':2,
                    'prompt_tokens_details':details}))
                self.assertEqual(result['input_tokens'],7)
                self.assertEqual(result['output_tokens'],2)
                self.assertNotIn('input_token_details',result)


if __name__=='__main__':unittest.main()
