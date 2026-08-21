def valid_lesson_dict():
    return {
        "source": {"sourceFile": "fixture(1차시 분량).pdf", "sha256": "fixture-sha", "lessonCount": 1},
        "meta": {"title": "기체 압력", "subject": "화학", "standardCodes": ["[fixture]" ]},
        "visuals": [{
            "id": "visual-pressure", "sourcePage": 1, "figureLabel": "fixture",
            "purpose": "압력의 입자 모형", "reuseMode": "reconstruct",
            "entities": [{"id": "particle", "label": "기체 입자"}, {"id": "wall", "label": "용기 벽"}],
            "relationships": [{"id": "collision", "kind": "collides", "from": "particle", "to": "wall", "primitiveIds": ["particle-path"]}],
            "invariants": [{"id": "volume", "kind": "constant", "refs": ["particle-path"]}],
            "schema": {"scene": {"canvas": {"id": "pressure-scene", "width": 200, "height": 120}, "primitives": [{"kind": "path", "semanticId": "particle-path", "points": [[0.2, 0.3], [0.8, 0.7]]}]}},
            "axes": [], "units": []
        }],
        "periods": [{
            "question": "기체는 왜 압력을 나타내는가?",
            "objectives": ["기체 압력을 입자 충돌로 설명할 수 있다."],
            "phenomenon": {"visualId": "visual-pressure", "body": "밀폐 용기에 기체를 더 넣으면 압력이 증가한다."},
            "observations": ["기체의 양이 증가했다.", "용기 부피는 일정하다."],
            "concepts": [
                {"id": "concept-pressure", "term": "기체 압력", "explanation": "기체 입자가 용기 벽에 충돌하여 단위 면적에 가하는 힘이다."},
                {"id": "concept-collision", "term": "충돌 빈도", "explanation": "입자가 벽에 부딪히는 횟수는 압력에 영향을 준다."},
            ],
            "causalChain": {
                "phenomenon": "압력이 증가한다.", "change": "단위 부피의 입자 수가 증가한다.",
                "cause": "벽과의 충돌 빈도가 증가한다.", "representation": "P는 같은 T, V에서 n에 비례한다.",
                "application": "같은 용기에 기체를 더 넣을 때 압력 변화를 예측한다."
            },
            "representations": [{"id": "relation-pressure", "kind": "equation", "body": "P ∝ n (T, V 일정)", "requires": ["concept-pressure", "concept-collision"]}],
            "workedExample": {"id": "worked-1", "prompt": "입자 수가 두 배가 되면 압력은?", "requires": ["concept-pressure", "relation-pressure"], "steps": ["일정한 T와 V를 확인한다.", "P ∝ n을 적용한다."], "answer": "두 배"},
            "guidedPractice": [{"id": "guided-1", "prompt": "입자 수가 세 배이면 압력은?", "requires": ["concept-pressure", "relation-pressure"], "answer": "세 배"}],
            "independentPractice": [{"id": "independent-1", "prompt": "압력 증가를 충돌로 설명하라.", "requires": ["concept-pressure"], "answer": "충돌 빈도가 증가하기 때문이다."}],
            "exitCheck": [{"id": "exit-1", "prompt": "기체 압력의 원인을 한 문장으로 쓰라.", "requires": ["concept-pressure"], "answer": "기체 입자의 벽 충돌이다."}]
        }],
        "teacherNotes": [{"period": 1, "emphasis": "힘이 아니라 단위 면적당 힘임을 강조한다.", "misconception": "입자가 정지해 압력을 만든다고 생각한다."}]
    }
