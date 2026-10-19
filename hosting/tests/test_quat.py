"""Кватернион из матрицы поворота (nwd2glb._quat_from_R): круговая проверка по всем ветвям Шеппарда."""
import os
import sys

import numpy as np

sys.path.insert(0, os.environ.get("NWD_SCRIPTS", "/app/scripts/nwd"))

import nwd2glb


def random_rotations(n, seed=11):
    """Равномерно случайные повороты (QR от нормального шума), det = +1."""
    q, r = np.linalg.qr(np.random.default_rng(seed).normal(size=(n, 3, 3)))
    q = q * np.sign(np.diagonal(r, axis1=1, axis2=2))[:, None, :]
    q[np.linalg.det(q) < 0, :, 0] *= -1
    return q


def quat_to_R(q):
    x, y, z, w = q.T
    return np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
                     2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
                     2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], axis=1).reshape(-1, 3, 3)


def test_quat_roundtrip_all_branches():
    R = random_rotations(5000)
    err = np.abs(quat_to_R(nwd2glb._quat_from_R(R)) - R).max(axis=(1, 2))
    assert err.max() < 1e-9, f"максимальная ошибка {err.max():.3g}, плохих {(err > 1e-9).sum()} из {len(R)}"


def test_quat_branch_coverage():
    """Случайные повороты задевают все четыре ветви (иначе круговая проверка ничего не стоит)."""
    R = random_rotations(5000)
    d = np.stack([np.einsum('nii->n', R), R[:, 0, 0], R[:, 1, 1], R[:, 2, 2]], axis=1)
    assert set(np.argmax(d, axis=1).tolist()) == {0, 1, 2, 3}
