from copy import deepcopy
import unittest
from research_agent.semantic_probabilities import probability_mass_metadata
from research_agent.evidence_judgments import validate_answers,QUESTIONS
from research_agent.evidence_memory import exact_excerpts

class ProbabilityCompatibilityTests(unittest.TestCase):
    def test_observed_jev_two_decimal_mass_kept_raw_and_explicitly_flagged(self):
        p={'cancelled':0.,'delayed':0.,'occurred':.93,'planned':.01,'uncertain':.05};original=deepcopy(p)
        r=probability_mass_metadata(p,'occurred','jev')
        self.assertEqual(p,original);self.assertEqual(r['probability_mass'],.99)
        self.assertEqual(r['probability_representation'],'bounded_two_decimal_rounding_compatible')
        self.assertFalse(r['probabilities_normalized'])
    def test_boundary_errors_and_impossible_selected_maximum_are_rejected(self):
        for p,selected in [({'a':0.,'b':0.},'a'),({'a':.99,'b':0.},'a'),
                ({'a':.9,'b':.9},'a'),({'a':.4,'b':.59},'a'),({'a':float('nan'),'b':0.},'a')]:
            with self.subTest(p=p),self.assertRaises(ValueError):probability_mass_metadata(p,selected,'jev')
    def test_more_precise_bad_mass_and_other_providers_remain_strict(self):
        for p,provider in [({'a':.501,'b':.49},'jev'),({'a':.5,'b':.49},'doubao')]:
            with self.assertRaises(ValueError):probability_mass_metadata(p,'a',provider)
    def test_choice_feasibility_and_exact_normalized_distribution(self):
        self.assertEqual(probability_mass_metadata({'a':.51,'b':.5},'a','jev')['probability_mass'],1.01)
        self.assertEqual(probability_mass_metadata({'a':.7,'b':.3},'a','jev')['probability_representation'],'normalized_within_tolerance')
    def test_low_confidence_dimension_not_promoted_by_format_compatibility(self):
        questions=QUESTIONS['classification'];answers={}
        for name,spec in questions.items():
            options=list(spec['criteria']);selected=options[0]
            answers[name]={'type':'choice','choice':selected,'confidence':.3,
                'probabilities':{k:1. if k==selected else 0. for k in options}}
        answers['event_status']={'type':'choice','choice':'occurred','confidence':.91,
            'probabilities':{'cancelled':0.,'delayed':0.,'occurred':.93,'planned':.01,'uncertain':.05}}
        r=validate_answers({'model':'jev-1.14.0','answers':answers},questions,'jev')
        self.assertEqual(r['topic']['confidence'],.3);self.assertEqual(r['event_status']['confidence'],.91)

class ExcerptRetentionTests(unittest.TestCase):
    def test_semantic_hint_does_not_replace_content_anchored_lexical_answer(self):
        import hashlib
        text=('Generic overview. '*200)+'The reserve proposal requires Treasury assets as backing. '+('Other details. '*200)
        query='reserve proposal Treasury backing';baseline=exact_excerpts(text,query)
        hint={'start':0,'end':1400,'sha256':hashlib.sha256(text[:1400].encode()).hexdigest()}
        new=exact_excerpts(text,query,hints=[hint])
        self.assertTrue(all(any(part['text']==old['text'] for part in new) for old in baseline))
        self.assertIn('Treasury assets as backing',' '.join(p['text'] for p in new))
        self.assertLessEqual(sum(p['end']-p['start'] for p in new),1600)

if __name__=='__main__':unittest.main()
