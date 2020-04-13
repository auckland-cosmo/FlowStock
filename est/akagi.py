from typing import Tuple

import numpy as np  # type: ignore
import scipy.optimize as opt  # type: ignore


class Akagi:

    """
    Use the method of Akagi et al to estimate movement
    """

    def __init__(self, N: np.ndarray, d: np.ndarray):

        self.N: np.ndarray = N
        # array of distances
        self.d: np.ndarray = d

        self.T: int = N.shape[0]
        num_cells = N.shape[1]
        self.num_cells: int = num_cells

        # Distance threshold
        self.K: float = 1

        # List of indices of neighbors of respective cells
        self.gamma: np.ndarray = self.gamma_calc()
        # Gamma excluding self
        self.gamma_exc: np.ndarray = self.gamma.copy()
        np.fill_diagonal(self.gamma_exc, False)

        # self.M is the main output of the algorithm
        self.M: np.ndarray = np.zeros((self.T - 1, num_cells, num_cells), dtype=int)
        for i in range(self.M.shape[0]):
            np.fill_diagonal(self.M[i], N[i])  # Default to no movement
        # self.M: np.ndarray = np.random.randint(
        #     0,
        #    N.max(),
        #    (self.T - 1, num_cells, num_cells),
        # )

        # Initial guesses for parameters
        self.pi: np.ndarray = np.ones(num_cells) / 2
        self.s: np.ndarray = np.ones(num_cells) / 2
        self.beta: float = 1.0e1

        self.lamda = 1000

    def exact_inference(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float]:
        """
        Estimate the movement of people in N and internal parameters
        """

        step = 0
        L = self.likelihood(self.M, self.pi, self.s, self.beta)
        L_old = L - 1

        while L > L_old:
            print("step # ", step, ", L = ", L)
            print("M[0] = \n", self.M[0])
            print("pi = ", self.pi)
            print("s = ", self.s)
            print("beta = ", self.beta)

            self.update_M()

            self.update_pi()

            self.update_s_beta()

            L_old, L = L, self.likelihood(self.M, self.pi, self.s, self.beta)
            step += 1

        return self.M, self.pi, self.s, self.beta

    def neg_likelihood_flat(self, M, pi, s, beta) -> float:
        """
        Calculate likelihood with flattened M

        Needed for `scipy.optimize.minimize` to work right
        """

        M_reshaped = np.reshape(M, self.M.shape)

        return -self.likelihood(M_reshaped, pi, s, beta)

    def likelihood(self, M, pi, s, beta) -> float:
        """
        Calculate  likelihood
        """

        d = self.d

        sexp = s * np.exp(-beta * d)

        term_0 = np.log(1 - pi)[np.newaxis, ...] * M.diagonal(axis1=1, axis2=2)
        assert term_0.shape == (self.T - 1, self.num_cells)

        term_1 = (
            # TODO: Handle 0 in log
            np.log((pi + (pi == 0))[np.newaxis, ..., np.newaxis])
            + np.log(s[np.newaxis, np.newaxis, ...])
            - beta * d[np.newaxis, ...]
            - np.log(sexp.sum(axis=1, where=[self.gamma_exc]))[
                np.newaxis, ..., np.newaxis
            ]
        ) * M
        assert term_1.shape == (self.T - 1, self.num_cells, self.num_cells)

        # TODO: Is this the best way to handle zeros in log?
        term_2 = M * (1 - np.log(M + (M == 0)))
        assert term_2.shape == (self.T - 1, self.num_cells, self.num_cells)

        term_3 = -self.lamda / 2 * self.cost(M, self.N)
        assert term_3.shape == ()

        out = 0
        out += term_0.sum(axis=(0, 1))
        out += term_1.sum(axis=2, where=self.gamma_exc).sum(axis=(0, 1))
        out += term_2.sum(axis=(0, 1, 2))
        out += term_3

        return out

    def cost(self, M, N):
        """
        Cost function
        """

        out = (
            (np.abs(N[:-1] - M.sum(axis=2)) ** 2).sum(axis=1)
            + (np.abs(N[1:] - M.sum(axis=1)) ** 2).sum(axis=1)
        ).sum(axis=0)

        return out

    def update_M(self):

        # TODO: bounds can be much more precise
        bounds = [(0, self.N.max() * 1.5) for _ in range(self.M.size)]

        result = opt.minimize(
            self.neg_likelihood_flat,
            # Use current M as initial guess
            x0=self.M,
            args=(self.pi, self.s, self.beta),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxfun": 15_000_000},
        )

        try:
            assert result.success
        except AssertionError:
            print("Error searching for minimum M")
            print(result.message)

        self.M = np.reshape(result.x, self.M.shape)

    def update_pi(self):

        numer = self.M.sum(where=self.gamma_exc, axis=2).sum(axis=0)
        denom = self.M.sum(axis=2).sum(axis=0)

        assert numer.shape == denom.shape == self.pi.shape

        self.pi = numer / denom

    def update_s_beta(self):

        s_u = self.s
        beta_u = self.beta

        step = 0

        f_new = self.f(s_u, beta_u)
        f_old = f_new - 1

        while f_old < f_new:
            print("s, beta step #", step, "s_u = ", s_u, ", beta_u = ", beta_u)

            # Update s
            s_u = self.A() / (
                self.C_u(s_u, beta_u)[..., np.newaxis] * np.exp(-beta_u * self.d)
            ).sum(where=self.gamma_exc, axis=1)

            # # Update beta
            # beta_u_res = opt.minimize(
            #     lambda beta_u_: - self.f_u(self.s, self.beta, s_u, beta_u_),
            #     x0=beta_u,
            #     method='CG',
            #     # bounds=(0, 100),
            # )
            # try:
            #     assert beta_u_res.success
            #     beta_u = beta_u_res.x
            # except:
            #     print("Error maximizing wrt beta_u")
            #     print(beta_u_res.message)
            #     print("Bashing on regardless")

            f_old, f_new = f_new, self.f(s_u, beta_u)

        self.s = s_u
        self.beta = beta_u

    def f(self, s, beta):
        """
        Objective function for Minorization-Maximization Algorith
        """

        A = self.A()
        B = self.B()
        D = self.D()
        sexp_term = self.sexp_term(s, beta)

        out = (A * np.log(s) - B * np.log(sexp_term)).sum(axis=0) - beta * D

        return out

    def f_u(self, s, beta, s_u, beta_u):
        """
        Lower bound function for Minorization-Maximization Algorith
        """

        A = self.A()
        C_u = self.C_u(s_u, beta_u)
        D = self.D()
        sexp_term = self.sexp_term(s, beta)

        out = (A * np.log(s) - C_u * sexp_term).sum(axis=0) - beta * D

        return out

    def A(self):
        out = self.M.sum(where=self.gamma_exc, axis=(0, 1))
        assert out.shape == (self.num_cells,)

        return out

    def B(self):
        out = self.M.sum(where=self.gamma_exc, axis=(0, 2))
        assert out.shape == (self.num_cells,)

        return out

    def C_u(self, s_u, beta_u):
        out = self.B() / self.sexp_term(s_u, beta_u)
        assert out.shape == (self.num_cells,)

        return out

    def D(self):
        out = (
            (self.M * self.d[np.newaxis, ...])
            .sum(where=self.gamma_exc, axis=2)
            .sum(axis=(0, 1))
        )
        assert out.shape == ()

        return out

    def sexp_term(self, s, beta):
        out = (s * np.exp(-beta * self.d)).sum(where=self.gamma_exc, axis=1)
        assert out.shape == (self.num_cells,)

        return out

    def gamma_calc(self):

        gamma = self.d <= self.K

        return gamma
