from app.adapters.outbound.mongo.seed import KNOWLEDGE_INSTRUCTIONS as K


def test_knowledge_instructions_cover_pix_modalities_and_ptbr_queries():
    assert 'Pix comercial' in K and 'POS Digital' in K and 'Get Smart' in K
    assert 'duas buscas' in K and 'pt-BR' in K
