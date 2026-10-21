"""仅在沙箱运行：核对固定来源，直接测试上游的原始核心定义。"""

import ast
import hashlib
import json
import math
import sys
import unittest
from pathlib import Path
from typing import Any

SOURCE_SHA256 = "9562df56fc29a172dd9284e0d2ba68443b25e8733981fedf854d1f36cbc2e74b"
CORE_NAMES = {
    "EncoderDecoder",
    "Generator",
    "clones",
    "Encoder",
    "LayerNorm",
    "SublayerConnection",
    "EncoderLayer",
    "Decoder",
    "DecoderLayer",
    "subsequent_mask",
    "attention",
    "MultiHeadedAttention",
    "PositionwiseFeedForward",
    "Embeddings",
    "PositionalEncoding",
    "make_model",
}
CORE_IMPORTS = {
    "import torch",
    "import torch.nn as nn",
    "from torch.nn.functional import log_softmax, pad",
    "import math",
    "import copy",
}


def load_core() -> dict[str, Any]:
    source = Path(__file__).with_name("the_annotated_transformer.py")
    raw = source.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SOURCE_SHA256, "pinned upstream source changed"
    tree = ast.parse(raw)
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.ClassDef) and node.name in CORE_NAMES
    ]
    assert {node.name for node in definitions} == CORE_NAMES
    imports = [
        node
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom) and ast.unparse(node) in CORE_IMPORTS
    ]
    assert {ast.unparse(node) for node in imports} == CORE_IMPORTS
    namespace: dict[str, Any] = {"__name__": "pinned_transformer_core"}
    # 不改写函数体；只加载原有核心定义，避免运行教程的数据下载、训练及可视化示例。
    module = ast.Module(body=[*imports, *definitions], type_ignores=[])
    exec(compile(module, str(source), "exec"), namespace)
    return namespace


def core_suite(api: dict[str, Any]) -> unittest.TestSuite:
    torch = api["torch"]

    class CoreTests(unittest.TestCase):
        def setUp(self) -> None:
            torch.manual_seed(2026)
            self.model = api["make_model"](11, 13, N=2, d_model=16, d_ff=32, h=2, dropout=0)
            self.model.eval()
            self.src = torch.tensor([[1, 2, 3, 4], [4, 3, 2, 1]])
            self.target = torch.tensor([[1, 3, 5], [2, 4, 6]])
            self.src_mask = torch.ones(2, 1, 4, dtype=torch.bool)
            self.target_mask = api["subsequent_mask"](3)

        def output(self, src: Any = None, target: Any = None) -> Any:
            return self.model(
                self.src if src is None else src,
                self.target if target is None else target,
                self.src_mask,
                self.target_mask,
            )

        def test_01_causal_mask_is_triangular(self) -> None:
            actual = api["subsequent_mask"](4)
            expected = torch.tril(torch.ones(4, 4, dtype=torch.bool)).unsqueeze(0)
            torch.testing.assert_close(actual, expected)

        def test_02_scaled_attention_matches_known_scores(self) -> None:
            query = torch.eye(2).reshape(1, 1, 2, 2)
            values = torch.tensor([[[[1.0, 10.0], [3.0, 30.0]]]])
            output, weights = api["attention"](query, query, values)
            expected = torch.softmax(torch.eye(2) / math.sqrt(2), dim=-1).reshape(1, 1, 2, 2)
            torch.testing.assert_close(weights, expected)
            torch.testing.assert_close(output, expected @ values)

        def test_03_masked_attention_cannot_see_future_values(self) -> None:
            query = torch.randn(1, 1, 3, 4)
            values = torch.randn(1, 1, 3, 4)
            first, weights = api["attention"](query, query, values, self.target_mask)
            changed = values.clone()
            changed[:, :, 2] += 100
            second, _ = api["attention"](query, query, changed, self.target_mask)
            torch.testing.assert_close(first[:, :, :2], second[:, :, :2])
            self.assertTrue(torch.equal(weights[0, 0, 0, 1:], torch.zeros(2)))

        def test_04_multihead_projection_and_head_shapes(self) -> None:
            layer = api["MultiHeadedAttention"](2, 16, dropout=0)
            inputs = torch.randn(2, 4, 16)
            self.assertEqual(tuple(layer(inputs, inputs, inputs).shape), (2, 4, 16))
            self.assertEqual(tuple(layer.attn.shape), (2, 2, 4, 4))
            torch.testing.assert_close(layer.attn.sum(-1), torch.ones(2, 2, 4))

        def test_05_feed_forward_preserves_model_dimension(self) -> None:
            layer = api["PositionwiseFeedForward"](16, 32, dropout=0)
            self.assertEqual(tuple(layer(torch.randn(2, 4, 16)).shape), (2, 4, 16))

        def test_06_layernorm_and_residual_connection(self) -> None:
            inputs = torch.randn(2, 4, 16)
            norm = api["LayerNorm"](16)
            torch.testing.assert_close(norm(inputs).mean(-1), torch.zeros(2, 4), atol=1e-6, rtol=0)
            connection = api["SublayerConnection"](16, 0)
            torch.testing.assert_close(
                connection(inputs, lambda value: value), inputs + norm(inputs)
            )

        def test_07_embedding_scale_and_positional_encoding(self) -> None:
            embedding = api["Embeddings"](16, 11)
            torch.testing.assert_close(embedding(self.src), embedding.lut(self.src) * 4)
            positional = api["PositionalEncoding"](16, 0, max_len=8)
            output = positional(torch.zeros(2, 4, 16))
            torch.testing.assert_close(output[:, 0, 0::2], torch.zeros(2, 8))
            torch.testing.assert_close(output[:, 0, 1::2], torch.ones(2, 8))
            self.assertFalse(torch.equal(output[:, 0], output[:, 1]))

        def test_08_complete_encoder_decoder_forward(self) -> None:
            self.assertEqual(len(self.model.encoder.layers), 2)
            self.assertEqual(len(self.model.decoder.layers), 2)
            self.assertEqual(tuple(self.output().shape), (2, 3, 16))

        def test_09_generator_returns_normalized_log_probabilities(self) -> None:
            probabilities = self.model.generator(self.output())
            self.assertEqual(tuple(probabilities.shape), (2, 3, 13))
            self.assertTrue(torch.isfinite(probabilities).all())
            torch.testing.assert_close(probabilities.exp().sum(-1), torch.ones(2, 3))

        def test_10_decoder_prefix_is_invariant_to_future_tokens(self) -> None:
            changed = self.target.clone()
            changed[:, -1] = (changed[:, -1] + 1) % 13
            torch.testing.assert_close(self.output()[:, :2], self.output(target=changed)[:, :2])

        def test_11_cross_attention_depends_on_source_memory(self) -> None:
            changed = self.src.clone()
            changed[:, 0] = 9
            self.assertFalse(torch.allclose(self.output(), self.output(src=changed)))

        def test_12_backward_reaches_encoder_and_cross_attention(self) -> None:
            probabilities = self.model.generator(self.output())
            labels = torch.tensor([[1, 2, 3], [4, 5, 6]])
            loss = -probabilities.gather(-1, labels.unsqueeze(-1)).mean()
            loss.backward()
            for weight in [
                self.model.encoder.layers[0].self_attn.linears[0].weight,
                self.model.decoder.layers[0].src_attn.linears[0].weight,
            ]:
                self.assertIsNotNone(weight.grad)
                self.assertTrue(torch.isfinite(weight.grad).all())
                self.assertGreater(weight.grad.abs().sum().item(), 0)

    return unittest.defaultTestLoader.loadTestsFromTestCase(CoreTests)


def main() -> None:
    api = load_core()
    api["torch"].set_num_threads(1)
    api["torch"].set_num_interop_threads(1)
    result = unittest.TextTestRunner(verbosity=2).run(core_suite(api))
    print(
        json.dumps(
            {
                "source_sha256": SOURCE_SHA256,
                "torch": api["torch"].__version__,
                "device": "cpu",
                "tests_run": result.testsRun,
                "failures": len(result.failures),
                "errors": len(result.errors),
                "skipped": len(result.skipped),
            }
        )
    )
    assert result.testsRun == 12 and not result.skipped
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
