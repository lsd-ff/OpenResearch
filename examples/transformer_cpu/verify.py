"""仅在沙箱中运行：验证固定仓库组件与小样本训练，不声称复现 WMT 指标。"""

import inspect
import json

import numpy as np
from transformer.activations import LogSoftmax, Softmax
from transformer.layers.combined.positional_encoding import PositionalEncoding
from transformer.layers.combined.self_attention import MultiHeadAttention
from transformer.losses import CrossEntropy
from transformer.modules import Decoder, Encoder

SEED = 42
VOCAB = 12
DIMENSION = 16
HEADS = 2


def numerical_gradient(function, values, epsilon=1e-5):
    gradient = np.zeros_like(values)
    for index in np.ndindex(values.shape):
        original = values[index]
        values[index] = original + epsilon
        upper = function(values)
        values[index] = original - epsilon
        lower = function(values)
        values[index] = original
        gradient[index] = (upper - lower) / (2 * epsilon)
    return gradient


def activation_gradient(activation):
    values = np.random.default_rng(SEED).normal(size=(2, 3, 4))
    upstream = np.random.default_rng(SEED + 1).normal(size=values.shape)
    expected = numerical_gradient(lambda x: float((activation.forward(x) * upstream).sum()), values)
    activation.forward(values)
    return float(np.max(np.abs(activation.backward(upstream) - expected)))


def cross_entropy_gradient_error():
    # 相同 logits 会暴露按“值是否出现”而非按目标位置选择梯度的问题。
    values = np.zeros((2, 3), dtype=np.float64)
    targets = np.array([0, 1])
    loss = CrossEntropy()
    expected = numerical_gradient(lambda x: float(loss.loss(x, targets).mean()), values)
    return float(np.max(np.abs(loss.derivative(values, targets) - expected)))


def correct_gradient_reductions():
    evidence = {}
    for activation_type in (Softmax, LogSoftmax):
        before = activation_gradient(activation_type())
        if before > 1e-6:
            original = activation_type.backward

            def corrected(self, gradient, implementation=original):
                # 激活函数不负责 loss 的 batch mean，补偿上游重复除法；仍调用原实现。
                return implementation(self, gradient) * self.x.shape[0]

            activation_type.backward = corrected
        after = activation_gradient(activation_type())
        assert after < 1e-6, (activation_type.__name__, before, after)
        evidence[activation_type.__name__] = {"before_max_error": before, "after_max_error": after}

    before = cross_entropy_gradient_error()

    def indexed_derivative(self, logits, targets):
        gradient = np.exp(self.log_softmax.forward(logits))
        gradient[np.arange(len(targets)), targets] -= 1
        if self.ignore_index is not None:
            gradient[targets == self.ignore_index] = 0
        return gradient / len(targets)

    if before > 1e-6:
        CrossEntropy.derivative = indexed_derivative
    after = cross_entropy_gradient_error()
    assert after < 1e-6, ("CrossEntropy", before, after)
    evidence["CrossEntropy"] = {"before_max_error": before, "after_max_error": after}
    return evidence


def make_models(seed):
    np.random.seed(seed)
    arguments = {
        "heads_num": HEADS,
        "layers_num": 1,
        "d_model": DIMENSION,
        "d_ff": 32,
        "dropout": 0,
        "max_length": 16,
        "data_type": np.float64,
    }
    return Encoder(src_vocab_size=VOCAB, **arguments), Decoder(trg_vocab_size=VOCAB, **arguments)


def masks(batch, source_length, target_length):
    source = np.ones((batch, 1, source_length), dtype=bool)
    target = np.broadcast_to(
        np.tril(np.ones((target_length, target_length), dtype=bool)),
        (batch, target_length, target_length),
    ).copy()
    return source, target


def check_attention():
    np.random.seed(SEED)
    inputs = np.random.normal(size=(2, 5, DIMENSION))
    attention = MultiHeadAttention(DIMENSION, HEADS, dropout=0, data_type=np.float64)
    mask = np.broadcast_to(np.tril(np.ones((5, 5))), (2, 5, 5)).copy()
    output, weights = attention.forward(inputs, inputs, inputs, mask, training=False)
    reference_heads = []
    errors = []
    for head in range(HEADS):
        region = slice(head * 8, (head + 1) * 8)
        query = inputs @ attention.Q_linear.w[:, region]
        key = inputs @ attention.K_linear.w[:, region]
        value = inputs @ attention.V_linear.w[:, region]
        scores = query @ key.transpose(0, 2, 1) / np.sqrt(8)
        scores = np.where(mask, scores, -np.inf)
        exponential = np.exp(scores - scores.max(axis=-1, keepdims=True))
        expected = exponential / exponential.sum(axis=-1, keepdims=True)
        errors.append(float(np.max(np.abs(weights[:, head] - expected))))
        reference_heads.append(expected @ value)
    reference = (
        np.concatenate(reference_heads, axis=-1) @ attention.O_linear.w + attention.O_linear.b
    )
    np.testing.assert_allclose(output, reference, atol=1e-8, rtol=1e-8)
    np.testing.assert_allclose(weights.sum(axis=-1), 1, atol=1e-8)
    assert output.shape == (2, 5, DIMENSION) and weights.shape == (2, HEADS, 5, 5)
    assert np.max(np.abs(np.triu(weights, k=1))) == 0
    changed = inputs.copy()
    changed[:, 3:] += 100
    second, _ = attention.forward(changed, changed, changed, mask, training=False)
    np.testing.assert_allclose(output[:, :3], second[:, :3], atol=1e-8, rtol=1e-8)
    return {
        "formula_max_error": max(errors),
        "output_shape": list(output.shape),
        "causal_future_weight": 0.0,
    }


def check_positions():
    actual = PositionalEncoding(16, DIMENSION, 0, np.float64).forward(np.zeros((1, 8, DIMENSION)))
    expected = np.empty_like(actual)
    for position in range(8):
        for dimension in range(DIMENSION):
            angle = position / (10000 ** (2 * (dimension // 2) / DIMENSION))
            expected[0, position, dimension] = (
                np.sin(angle) if dimension % 2 == 0 else np.cos(angle)
            )
    error = float(np.max(np.abs(actual - expected)))
    assert error < 1e-10
    return {"max_error": error}


def check_encoder_decoder():
    encoder, decoder = make_models(SEED)
    source = np.array([[3, 4, 5, 6], [6, 7, 8, 9]])
    target = np.array([[1, 3, 4, 5, 6], [1, 6, 7, 8, 9]])
    source_mask, target_mask = masks(2, 4, 5)
    memory = encoder.forward(source, source_mask, False)
    output, attention = decoder.forward(target, target_mask, memory, source_mask, False)
    assert memory.shape == (2, 4, DIMENSION) and output.shape == (2, 5, VOCAB)
    assert attention.shape == (2, HEADS, 5, 4) and np.isfinite(output).all()
    changed = target.copy()
    changed[:, 3:] = 10
    second, _ = decoder.forward(changed, target_mask, memory, source_mask, False)
    np.testing.assert_allclose(output[:, :3], second[:, :3], atol=1e-7, rtol=1e-7)
    altered_memory = encoder.forward(np.full_like(source, 10), source_mask, False)
    third, _ = decoder.forward(target, target_mask, altered_memory, source_mask, False)
    difference = float(np.max(np.abs(output - third)))
    assert difference > 1e-6
    return {
        "encoder_shape": list(memory.shape),
        "logits_shape": list(output.shape),
        "cross_attention_source_difference": difference,
        "decoder_causality": "passed",
    }


class BoundedSGD:
    """使用原仓库 update_weights 接口；本测试的 SGD 不冒充论文 Adam/Noam 配置。"""

    def update(self, gradient, weights, velocity, momentum, velocity_hat, momentum_hat, layer):
        norm = np.linalg.norm(gradient)
        assert np.isfinite(norm)
        scale = min(1, 5 / (float(norm) + 1e-12))
        return weights - 0.1 * scale * gradient, velocity, momentum, velocity_hat, momentum_hat


def train_task(name, seed):
    encoder, decoder = make_models(seed)
    optimizer = BoundedSGD()
    encoder.set_optimizer(optimizer)
    decoder.set_optimizer(optimizer)
    rng = np.random.default_rng(seed)
    source = rng.integers(3, VOCAB, size=(8, 4))
    expected = source.copy() if name == "copy" else source[:, ::-1].copy()
    expected = np.concatenate([expected, np.full((8, 1), 2)], axis=1)
    target = np.concatenate([np.ones((8, 1), dtype=int), expected[:, :-1]], axis=1)
    source_mask, target_mask = masks(8, 4, 5)
    objective = CrossEntropy()
    original_weights = encoder.token_embedding.w.copy()
    curve = []
    for step in range(201):
        memory = encoder.forward(source, source_mask, True)
        logits, _ = decoder.forward(target, target_mask, memory, source_mask, True)
        loss = float(objective.loss(logits.reshape(-1, VOCAB), expected.reshape(-1)).mean())
        assert np.isfinite(loss)
        if step % 10 == 0:
            curve.append({"step": step, "loss": loss})
        if step == 200:
            break
        gradient = objective.derivative(logits.reshape(-1, VOCAB), expected.reshape(-1))
        decoder.backward(gradient.reshape(logits.shape))
        encoder.backward(decoder.encoder_error)
        encoder.update_weights()
        decoder.update_weights()
    delta = float(np.linalg.norm(encoder.token_embedding.w - original_weights))
    assert delta > 0 and curve[-1]["loss"] < 0.7 * curve[0]["loss"], (name, curve)
    return {
        "task": name,
        "steps": 200,
        "initial_loss": curve[0]["loss"],
        "final_loss": curve[-1]["loss"],
        "training_token_accuracy": float((logits.argmax(axis=-1) == expected).mean()),
        "encoder_embedding_change_norm": delta,
        "curve": curve,
    }


def main():
    result = {
        "scope": "tiny_cpu_core_reproduction",
        "seed": SEED,
        "source_files": {
            kind.__name__: inspect.getfile(kind)
            for kind in (Encoder, Decoder, MultiHeadAttention, PositionalEncoding)
        },
        "gradient_regressions": correct_gradient_reductions(),
        "attention": check_attention(),
        "positional_encoding": check_positions(),
        "encoder_decoder": check_encoder_decoder(),
    }
    for name, seed in (("copy", SEED), ("reverse", SEED + 1)):
        training = train_task(name, seed)
        result[name] = training
        print("TRAINING_RESULT " + json.dumps(training), flush=True)
    assert all(
        path.startswith("/tmp/openresearch/repository/") for path in result["source_files"].values()
    )
    print("TRANSFORMER_RESULT " + json.dumps(result), flush=True)
    print("TRANSFORMER_CORE_TESTS_PASSED", flush=True)


if __name__ == "__main__":
    main()
