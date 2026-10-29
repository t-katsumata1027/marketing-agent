"""本体固定TSから独立Node計算した値。Agent実装を期待値の生成に使わない。"""
import hashlib,unittest
from src.mina.routine_request_recovery import expected_hash,CONTRACT_COMMIT

# source: canonical.ts 4ed5b397703e62c7e125e7073273f6d025fcd7df3300ed2a0bdf1aeefbc821d2
# handlers.ts 058fce6426392ab0e683420daeada3b5e302a5d4f7d388e1e12c71ee02e9cd43
# agent-api.ts 06ff2eed876d0a89e630ed65e336efcdeb2ac29104376d571f158a1e10e499b8
class HashVectorsTests(unittest.TestCase):
    def test_independent_official_vectors_three_operations(self):
        self.assertEqual(CONTRACT_COMMIT,'f4aee73ecc9ad2c784c6196fb4d04009812037d6')
        image=bytes.fromhex('ffd8ff00') # hash計算専用。実APIへ送らない。
        cases=[('meal_register',{'contract_version':'mina-write-v1','meal_type':'breakfast','logged_at':'2027-01-01T07:30:12.3+09:00','comment':'\ufeff e\u0301と朝食\u3000','expected_state_version':'sv1_'+'1'*64},'ih1_1a36609369e6c8692556b115c1c4398e2738389579d3fd64bfce73826d499809'),
          ('meal_correction',{'contract_version':'mina-write-v1','reason':'codex_secondary_review','share_url':'https://www.log-eats.com/s/a_b-7','foods':[{'name':'\ufeffごはん\u00a0','amount':'\u200980g\u2009','calories':120.5,'protein':2.125,'fat':0.25,'carbs':28.5},{'name':'\u3000カ\u3099ーリック\ufeff','amount':'\u00a0\t','calories':0,'protein':0,'fat':0,'carbs':.000001}],'comment':'\u3000か\u3099んは\u3099る\u00a0','expected_state_version':'sv1_'+'2'*64},'ih1_c48803dc5cafe7f3d093d2ea5a2f5b489bd40e3602fe32c05d6e3a60237e6e7f'),
          ('daily_share',{'contract_version':'mina-write-v1','date':'2027-01-01','expected_meal_count':3,'comment':'\u2009一日 e\u0301\ufeff','expected_state_version':'sv1_'+'3'*64},'ih1_feda79233589f4136a2ceb3132230681eaa332468f0ce8b461973dddfced8821')]
        for op,payload,expected in cases:
            with self.subTest(operation=op):self.assertEqual(expected_hash({'operation':op,'payload':payload,'image':image,'image_sha256':hashlib.sha256(image).hexdigest()}),expected)
        p={**cases[1][1],'share_url':'https://www.log-eats.com/s/a%5Fb-7'}
        self.assertEqual(expected_hash({'operation':'meal_correction','payload':p}),cases[1][2])
