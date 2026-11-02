"""Mechanical evaluation labels are not a semantic truth judge."""
import unittest
from research_agent.memory_answer_evaluation import FrozenAnswer,score_answer

class MemoryAnswerTests(unittest.TestCase):
    def test_quotation_and_reference_are_checked_against_the_selected_context(self):
        rows=[{'record_id':'r','excerpt':'A saved plan is not a confirmed outcome.'}]
        answer=FrozenAnswer(response='A plan.',decision='answer',limitations=['Synthetic fixture'],
            claims=[{'statement':'A plan.','evidence_id':'r','quote':'A saved plan'}])
        score=score_answer(answer,rows,'answer','confirmed outcome')
        self.assertEqual(score['verbatim_quotes'],1)
        self.assertIsNone(score['semantic_correctness'])
        answer.claims[0].quote='An invented passage'
        self.assertEqual(score_answer(answer,rows,'answer','outcome')['verbatim_quotes'],0)
        answer.claims[0].evidence_id='unselected'
        self.assertEqual(score_answer(answer,rows,'answer','outcome')['eligible_citations'],0)

    def test_refusal_does_not_get_credit_as_a_required_factual_answer(self):
        answer=FrozenAnswer(response='Insufficient evidence.',decision='insufficient_evidence',limitations=['Missing source'])
        score=score_answer(answer,[],'answer','required')
        self.assertFalse(score['mode_matches_ai_authored_rubric'])
        self.assertEqual(score['claims'],0)
        self.assertFalse(score['required_passage_in_selected_context'])

if __name__=='__main__':unittest.main()
