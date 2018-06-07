import numpy as np
from numba import njit
from scipy import sparse
from tqdm import trange
from scipy.sparse import linalg as splinalg
from scipy.stats import hmean

def backtrack(
        f_t, f_grad, x_t, d_t, g_t, L_t,
        gamma_max=1, ratio_increase=2., ratio_decrease=0.999,
        max_iter=100):
    d2_t = d_t.T.dot(d_t)
    for i in range(max_iter):
        step_size = min(g_t / (d2_t * L_t), gamma_max)
        rhs = f_t - step_size * g_t + 0.5 * (step_size**2) * L_t * d2_t
        f_next, grad_next = f_grad(x_t + step_size * d_t)
        if f_next <= rhs:
            if i == 0:
                L_t *= ratio_decrease
            break
    else:
        L_t *= ratio_increase
    return step_size, L_t, f_next, grad_next


# do line search by bysection
def approx_ls(f_t, f_grad, x_t, d_t, g_t, L_t,
        gamma_max=1, ratio_increase=2., ratio_decrease=0.99,
        max_iter=100):
    # approximate line search
    def obj(gamma):
        f_next, grad = f_grad(x_t + gamma * d_t)
        grad_gamma = d_t.T.dot(grad)
        return f_next, grad_gamma
    lbracket = 0
    grad_lbracket = obj(lbracket)[1]
    rbracket = gamma_max
    grad_rbracket = obj(rbracket)[1]
    for _ in range(max_iter):
        assert grad_lbracket * grad_rbracket <= 0
        c = (lbracket + rbracket) / 2.
        fc, grad_c = obj(c)
        if grad_c * grad_lbracket >= 0:
            lbracket = c
            grad_lbracket = grad_c
        else:
            rbracket = c
            grad_rbracket = grad_c
        if fc < f_t:
            break
    out = (lbracket + rbracket) / 2.
    return out

def approx_ls_trace(f_t, f_grad, x_t, d_t, g_t, L_t,
        gamma_max=1, ratio_increase=2., ratio_decrease=0.99,
        max_iter=100):
    # approximate line search
    def obj(gamma):
        f_next, grad = f_grad(x_t + gamma * d_t.reshape(x_t.shape))
        grad_gamma = grad.multiply(d_t).sum()
        return f_next, grad_gamma, grad_gamma
    lbracket = 0
    grad_lbracket = obj(lbracket)[1]
    rbracket = gamma_max
    grad_rbracket = obj(rbracket)[1]
    for _ in range(max_iter):
        assert grad_lbracket * grad_rbracket <= 0
        c = (lbracket + rbracket) / 2.
        fc, grad_c, grad_gamma = obj(c)
        if grad_c * grad_lbracket >= 0:
            lbracket = c
            grad_lbracket = grad_c
        else:
            rbracket = c
            grad_rbracket = grad_c
        if fc < f_t:
            break
    if fc < f_t + 0.01 * c * grad_gamma:
        return c
    out = (lbracket + rbracket) / 2.
    return out



def approx_ls_armijo(f_t, f_grad, x_t, d_t, g_t, L_t,
        gamma_max=1, ratio_increase=2., ratio_decrease=0.99,
        max_iter=100):
    # approximate line search
    def obj(gamma):
        f_next, grad = f_grad(x_t + gamma * d_t)
        grad_gamma = d_t.T.dot(grad)
        return f_next, grad_gamma, grad

    lbracket = 0
    grad_lbracket = obj(lbracket)[1]
    rbracket = gamma_max
    grad_rbracket = obj(rbracket)[1]
    for _ in range(max_iter):
        assert grad_lbracket * grad_rbracket <= 0
        c = (lbracket + rbracket) / 2.
        fc, grad_c, grad_f = obj(c)
        if grad_c * grad_lbracket >= 0:
            lbracket = c
            grad_lbracket = grad_c
        else:
            rbracket = c
            grad_rbracket = grad_c
        if fc < f_t + 0.1 * c * d_t.dot(grad_f):
            break
    return c

def minimize_FW_L1(f_grad, x0, alpha, L_t=1, max_iter=100, tol=1e-12,
          ls_strategy='adaptive', callback=None):
    x_t = x0.copy()
    if callback is not None:
        callback(x_t)
    pbar = trange(max_iter)
    f_t, grad = f_grad(x_t)
    L_average = 0.
    for it in pbar:
        idx_oracle = np.argmax(np.abs(grad))
        mag_oracle = alpha * np.sign(-grad[idx_oracle])
        d_t = - x_t.copy()
        d_t[idx_oracle] += mag_oracle
        g_t = - d_t.T.dot(grad)
        if g_t <= tol:
            break
        if ls_strategy == 'adaptive':
            step_size, L_t, f_next, grad_next = backtrack(
                f_t, f_grad, x_t, d_t, g_t, L_t)
        elif ls_strategy == 'Lipschitz':
            d2_t = d_t.dot(d_t)
            step_size = min(g_t / (d2_t * L_t), 1)
            f_next, grad_next = f_grad(x_t + step_size * d_t)
        elif ls_strategy == 'approx_ls':
            step_size = approx_ls_armijo(
                f_t, f_grad, x_t, d_t, g_t, L_t)
            f_next, grad_next = f_grad(x_t + step_size * d_t)
        x_t += step_size * d_t
        if it % 10 == 0:
            pbar.set_postfix(tol=g_t, iter=it, step_size=step_size, L_t=L_t, L_average=L_average)

        f_t,  grad = f_next, grad_next
        L_average = L_t / (it + 1) + (it/(it+1)) * L_average
        if callback is not None:
            callback(x_t)
    pbar.close()
    return x_t


def minimize_FW_trace(A, x0, alpha, L_t=1, max_iter=100, tol=1e-12,
          ls_strategy='adaptive', callback=None):
    x_t = x0.copy()
    P = A.copy()
    P.data[:] = 1.
    if callback is not None:
        callback(x_t)
    pbar = trange(max_iter)
    LS_EPS = np.finfo(np.float).eps
    n_features = A.shape[0] * A.shape[1]
    beta = 1. / n_features

    def f_grad(x):
        tmp = A - P.multiply(x)
        f_t = 0.5 * tmp.multiply(tmp).mean()
        # if hasattr(x, 'multiply'):
        #       f_t += 0.5 * beta * (x.multiply(x)).sum()
        # else:
        #     f_t += 0.5 * beta * (x * x).sum()
        grad = - tmp / n_features
        return f_t, grad

    f_t, grad = f_grad(x_t)

    for it in pbar:

        u, s, vt = splinalg.svds(-grad, k=1, maxiter=1000)
        s_t = alpha * u.dot(vt)

        d_t = s_t - x_t
        g_t = - grad.multiply(d_t).sum()
        if g_t <= tol:
            break
        if ls_strategy == 'adaptive':
            d2_t = (d_t * d_t).sum()
            for i in range(100):
                step_size = min(g_t / (d2_t * L_t), 1.)
                rhs = f_t - step_size * g_t + 0.5 * (step_size ** 2) * L_t * d2_t
                f_next, grad_next = f_grad(x_t + step_size * d_t)
                if (f_next - f_t) / step_size <= -(g_t - 0.5 * step_size * L_t * d2_t) + LS_EPS / step_size:
                    if i == 0:
                        L_t *= 0.999
                    break
                else:
                    L_t *= 2.
        elif ls_strategy == 'Lipschitz':
            d2_t = (d_t * d_t).sum()
            step_size = min(g_t / (d2_t * L_t), 1)
            f_next, grad_next = f_grad(x_t + step_size * d_t)
        elif ls_strategy == 'approx_ls':
            step_size = approx_ls_trace(
                f_t, f_grad, x_t, d_t, g_t, L_t)
            f_next, grad_next = f_grad(x_t + step_size * d_t)
        else:
            raise ValueError
        x_t += step_size * d_t
        if it % 10 == 0:
            pbar.set_postfix(tol=g_t, L_t=L_t, step_size=step_size)

        f_t,  grad = f_next, grad_next
        if callback is not None:
            callback(x_t)
    pbar.close()
    return x_t





def minimize_PFW_trace(A, x0, alpha, L_t=1, max_iter=100, tol=1e-12,
          ls_strategy='adaptive', callback=None):
    x_t = x0.copy()
    P = A.copy()
    P.data[:] = 1.
    if callback is not None:
        callback(x_t)
    pbar = trange(max_iter)
    LS_EPS = np.finfo(np.float).eps
    n_features = A.shape[0] * A.shape[1]
    active_set = [np.zeros(A.shape)]
    coefs_active_set = [1.]
    L_t_mean = []

    def f_grad(x):
        tmp = A - P.multiply(x)
        f_t = 0.5 * tmp.multiply(tmp).mean()
        grad = - tmp / n_features
        return f_t, grad

    f_t, grad = f_grad(x_t)

    for it in pbar:
        # f_t, grad = f_grad(x_t)  # XXX

        u, s, vt = splinalg.svds(-grad, k=1)
        s_t = alpha * u.dot(vt)
        min_vt_st = np.inf
        idx_min_vt_st = -1
        atom_in_activeset = False
        for i in range(len(active_set)):
            tmp = s_t - active_set[i]
            tmp_norm = (tmp * tmp).max()
            if tmp_norm < min_vt_st:
                min_vt_st = tmp_norm
                idx_min_vt_st = i
        if min_vt_st < 1e-4:
            atom_in_activeset = True
            s_t = active_set[idx_min_vt_st]

        v_t = active_set[0]
        vt_loss = np.inf
        vt_idx = -1
        for i in range(len(active_set)):
            tmp_vt = active_set[i]
            tmp_vt_loss = grad.multiply(x_t - tmp_vt).sum()
            # print(tmp_vt_loss)
            if tmp_vt_loss <= vt_loss:
                v_t = tmp_vt
                vt_loss = tmp_vt_loss
                vt_idx = i
                # tmp = s_t + v_t
                # # print((tmp * tmp).mean(), len(active_set), vt_idx)
                # if vt_idx != 0:
                #     print('not zero')
                # if (tmp * tmp).max() < 1e-2:
                #     print(it, i, 'atom in active set')
        # if it > 10:
        #     1/0
        if vt_idx < 0:
            raise ValueError

        gamma_max = coefs_active_set[vt_idx]
        # print('gamma_max', gamma_max)
        d_t = s_t - v_t
        g_t = - grad.multiply(d_t).sum()

        if g_t <= tol:
            pass
            # g_t = 0.
            # break
        if ls_strategy == 'adaptive':
            d2_t = (d_t * d_t).sum()
            for i in range(100):
                step_size = min(g_t / (d2_t * L_t), gamma_max)
                f_next, grad_next = f_grad(x_t + step_size * d_t)
                if (f_next - f_t) / step_size <= -(g_t - 0.5 * step_size * L_t * d2_t) + LS_EPS / step_size:
                    if i == 0:
                        L_t *= 0.99
                    break
                else:
                    L_t *= 2.
        elif ls_strategy == 'Lipschitz':
            d2_t = (d_t * d_t).sum()
            step_size = min(g_t / (d2_t * L_t), gamma_max)
            f_next, grad_next = f_grad(x_t + step_size * d_t)
        elif ls_strategy == 'approx_ls':
            step_size = approx_ls(
                f_t, f_grad, x_t, d_t, g_t, L_t)
            f_next, grad_next = f_grad(x_t + step_size * d_t)
        else:
            raise ValueError

        x_t += step_size * d_t

        # update coefficients
        if atom_in_activeset:
            coefs_active_set[idx_min_vt_st] += step_size
        else:
            active_set.append(s_t)
            coefs_active_set.append(step_size)

        if step_size == gamma_max:
            active_set.pop(vt_idx)
            coefs_active_set.pop(vt_idx)
        else:
            coefs_active_set[vt_idx] -= step_size

        if it % 10 == 0:
            L_t_mean.append(L_t)
            pbar.set_postfix(tol=g_t, L_t=np.mean(L_t_mean), step_size=step_size, size_active=len(active_set))

        f_t,  grad = f_next, grad_next
        if callback is not None:
            callback(x_t)
    pbar.close()
    return x_t





@njit
def max_active(grad, active_set, n_features, include_zero=True):
    # find the index that most correlates with the gradient
    max_grad_active = - np.inf
    max_grad_active_idx = -1
    for j in range(n_features):
        if active_set[j]:
            if grad[j] > max_grad_active:
                max_grad_active = grad[j]
                max_grad_active_idx = j
    for j in range(n_features, 2 * n_features):
        if active_set[j]:
            if - grad[j % n_features] > max_grad_active:
                max_grad_active = - grad[j % n_features]
                max_grad_active_idx = j
    if include_zero:
        if max_grad_active < 0 and active_set[2 * n_features]:
            max_grad_active = 0.
            max_grad_active_idx = 2 * n_features
    return max_grad_active, max_grad_active_idx

# @profile
def minimize_PFW_L1(f_grad, x0, alpha, L_t=1, max_iter=1000, tol=1e-12,
          ls_strategy='adaptive', callback=None):
    x_t = np.zeros(x0.size)
    L0 = L_t
    if callback is not None:
        callback(x_t)

    n_features = x0.shape[0]
    active_set = np.zeros(2 * n_features + 1, dtype=np.bool)
    active_set[2 * n_features] = True
    # active_set_weights = np.zeros(2 * n_features + 1, dtype=np.float)
    # active_set_weights[-1] = 1.
    LS_EPS = np.finfo(np.float).eps
    weight_zero = 1.
    all_Lt = []

    pbar = trange(max_iter)
    num_bad_steps = 0
    f_t, grad = f_grad(x_t)
    for it in pbar:
        # f_t, grad = f_grad(x_t)
        idx_oracle = np.argmax(np.abs(grad))
        if grad[idx_oracle] > 0:
            idx_oracle += n_features
        mag_oracle = alpha * np.sign(-grad[idx_oracle % n_features])

        max_grad_active, max_grad_active_idx = max_active(
            grad, active_set, n_features)

        mag_away = alpha * np.sign(float(n_features - max_grad_active_idx))

        is_away_zero = (max_grad_active_idx == 2 * n_features)
        if is_away_zero:
            gamma_max = weight_zero
        else:
            gamma_max = np.abs(x_t[max_grad_active_idx % n_features]) / alpha
        assert gamma_max > 0

        g_t = grad[max_grad_active_idx % n_features] * mag_away - \
              grad[idx_oracle % n_features] * mag_oracle
        if g_t <= tol:
            break

        if idx_oracle == max_grad_active_idx:
            raise ValueError
        else:
            d2_t = 2 * (alpha ** 2)
        x_next = x_t.copy()
        if ls_strategy == 'adaptive':
            for i in range(100):
                step_size = min(g_t / (d2_t * L_t), gamma_max)
                rhs = - step_size * g_t + 0.5 * (step_size ** 2) * L_t * d2_t
                rhs2 = - step_size * (g_t - 0.5 * step_size * L_t * d2_t)

                x_next[idx_oracle % n_features] = x_t[idx_oracle % n_features] + step_size * mag_oracle
                if not is_away_zero:
                    x_next[max_grad_active_idx % n_features] = x_t[max_grad_active_idx % n_features] - step_size * mag_away
                f_next, grad_next = f_grad(x_next)
                if step_size < 1e-7:
                    break
                elif (f_next - f_t)/step_size <= -(g_t - 0.5 * step_size * L_t * d2_t) + LS_EPS/step_size:
                    if i == 0:
                        L_t *= 0.999
                    break
                else:
                    L_t *= 2
                # else:
                #     if L_t > 2 * L0:
                #         raise ValueError
                #     L_t *= 2
                    # print(i, L_t, f_next, rhs)
            # else:
            #     step_size = min(g_t / (d2_t * L_t), gamma_max)
            #     rhs = f_t - step_size * g_t + 0.5 * (step_size ** 2) * L_t * d2_t
            #     x_next[idx_oracle % n_features] = x_t[idx_oracle % n_features] + step_size * mag_oracle
            #     if not is_away_zero:
            #         x_next[max_grad_active_idx % n_features] = x_t[max_grad_active_idx % n_features] - step_size * mag_away
            #     f_next, grad_next = f_grad(x_next)
        elif ls_strategy == 'Lipschitz':
            step_size = min(g_t / (d2_t * L_t), gamma_max)
            x_next[idx_oracle % n_features] = x_t[idx_oracle % n_features] + step_size * mag_oracle
            if not is_away_zero:
                x_next[max_grad_active_idx % n_features] = x_t[max_grad_active_idx % n_features] - step_size * mag_away
            f_next, grad_next = f_grad(x_next)
        else:
            raise ValueError(ls_strategy)

        if L_t >= 1e10:
            raise ValueError
        # was it a drop step?
        # x_t[idx_oracle] += step_size * mag_oracle
        x_t = x_next
        active_set[idx_oracle] = (x_t[idx_oracle % n_features] != 0)

        if it > 0:
            if max_grad_active_idx == 2 * n_features:
                pass
            # x_t[max_grad_active_idx] -= step_size * mag_away
            if is_away_zero:
                weight_zero -= step_size
                if weight_zero == 0:
                    active_set[2 * n_features] = False
            else:
                active_set[max_grad_active_idx] = (x_t[max_grad_active_idx % n_features] != 0)

        f_t, grad = f_next, grad_next
        
        if gamma_max < 1 and step_size == gamma_max:
            num_bad_steps += 1

        # x_t += step_size * d_t
        if it % 100 == 0:
            all_Lt.append(L_t)
            pbar.set_postfix(
                tol=g_t, Lipschitz=L0, gmax=gamma_max, gamma=step_size, d2t=d2_t,
                L_t_mean=np.mean(all_Lt), L_t=L_t, L_t_harm=hmean(all_Lt),
                bad_steps_quot=(num_bad_steps) / (it+1))

        # if it > int(3e+06):
        #     import ipdb;
        #     ipdb.set_trace()

        if callback is not None:
            callback(x_t)
    pbar.close()
    return x_t


def minimize_AFW_L1(f_grad, x0, alpha, L_t=1, max_iter=1000, tol=1e-12,
          ls_strategy='adaptive', callback=None):
    x_t = np.zeros(x0.size)
    L0 = L_t
    if callback is not None:
        callback(x_t)

    n_features = x0.shape[0]
    active_set = np.zeros(2 * n_features + 1, dtype=np.bool)
    active_set[2 * n_features] = True
    # active_set_weights = np.zeros(2 * n_features + 1, dtype=np.float)
    # active_set_weights[-1] = 1.
    LS_EPS = np.finfo(np.float).eps
    weight_zero = 1.
    all_Lt = []

    pbar = trange(max_iter)
    f_t, grad = f_grad(x_t)
    for it in pbar:
        # f_t, grad = f_grad(x_t)
        idx_oracle = np.argmax(np.abs(grad))
        if grad[idx_oracle] > 0:
            idx_oracle += n_features
        mag_oracle = alpha * np.sign(-grad[idx_oracle % n_features])

        max_grad_active, max_grad_active_idx = max_active(
            grad, active_set, n_features)

        mag_away = alpha * np.sign(float(n_features - max_grad_active_idx))

        # if 
        # is_away_zero = (max_grad_active_idx == 2 * n_features)
        # if is_away_zero:
        #     gamma_max = weight_zero
        # else:
        #     gamma_max = np.abs(x_t[max_grad_active_idx % n_features]) / alpha
        # assert gamma_max > 0

        x_grad = grad.dot(x_t)
        gt_fw = - mag_oracle * grad[idx_oracle % n_features] + x_grad
        gt_a = - x_grad + mag_away * grad[max_grad_active_idx % n_features]

        assert gt_fw >= 0

        if gt_fw >= gt_a:
            # FW step
            d_t = - x_t.copy()
            d_t[idx_oracle % n_features] += mag_oracle
            gamma_max = 1.
            g_t = gt_fw
        else:
            # away step
            d_t = x_t.copy()
            d_t[max_grad_active_idx % n_features] -= mag_away
            alpha_v = np.abs(x_t[max_grad_active_idx % n_features]) / alpha
            gamma_max = alpha_v / (1. - alpha_v)
            g_t = gt_a

        if g_t <= tol:
            break

        d2_t = d_t.dot(d_t)

        if ls_strategy == 'adaptive':
            for i in range(100):
                step_size = min(g_t / (d2_t * L_t), gamma_max)
                rhs = - step_size * g_t + 0.5 * (step_size ** 2) * L_t * d2_t
                rhs2 = - step_size * (g_t - 0.5 * step_size * L_t * d2_t)

                x_next = x_t + step_size * d_t 
                # x_next[idx_oracle % n_features] = x_t[idx_oracle % n_features] + step_size * mag_oracle
                # if not is_away_zero:
                #     x_next[max_grad_active_idx % n_features] = x_t[max_grad_active_idx % n_features] - step_size * mag_away
                f_next, grad_next = f_grad(x_next)
                if step_size < 1e-7:
                    break
                elif (f_next - f_t)/step_size <= -(g_t - 0.5 * step_size * L_t * d2_t) + LS_EPS/step_size:
                    if i == 0:
                        L_t *= 0.999
                    break
                else:
                    # if L_t > 2 * L0:
                    #     raise ValueError
                    L_t *= 2
                    # print(i, L_t, f_next, rhs)
            # else:
            #     step_size = min(g_t / (d2_t * L_t), gamma_max)
            #     rhs = f_t - step_size * g_t + 0.5 * (step_size ** 2) * L_t * d2_t
            #     x_next[idx_oracle % n_features] = x_t[idx_oracle % n_features] + step_size * mag_oracle
            #     if not is_away_zero:
            #         x_next[max_grad_active_idx % n_features] = x_t[max_grad_active_idx % n_features] - step_size * mag_away
            #     f_next, grad_next = f_grad(x_next)
        elif ls_strategy == 'Lipschitz':
            step_size = min(g_t / (d2_t * L_t), gamma_max)
            x_next = x_t + step_size * d_t
            f_next, grad_next = f_grad(x_next)
        else:
            raise ValueError(ls_strategy)

        if L_t >= 1e10:
            raise ValueError
        # was it a drop step?
        # x_t[idx_oracle] += step_size * mag_oracle
        x_t = x_next
        active_set[idx_oracle] = (x_t[idx_oracle % n_features] != 0)

        active_set[max_grad_active_idx] = (x_t[max_grad_active_idx % n_features] != 0)

        f_t, grad = f_next, grad_next

        # x_t += step_size * d_t
        all_Lt.append(L_t)
        if it % 100 == 0:
            pbar.set_postfix(
                tol=g_t, Lipschitz=L0, gmax=gamma_max, gamma=step_size, d2t=d2_t,
                L_t_mean=np.mean(all_Lt), L_t=L_t, L_t_harm=hmean(all_Lt))

        # if it > int(3e+06):
        #     import ipdb;
        #     ipdb.set_trace()

        if callback is not None:
            callback(x_t)
    pbar.close()
    return x_t