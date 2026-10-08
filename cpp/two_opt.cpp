// C++ 2-opt. Same scan order, first-improvement rule and epsilon as
// routewise.solver.two_opt, so both return identical tours.

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <algorithm>
#include <cstddef>
#include <stdexcept>
#include <vector>

namespace py = pybind11;

namespace {
constexpr double kEps = 1e-12;
}

std::vector<int> two_opt(const std::vector<std::vector<double>> &matrix,
                         std::vector<int> tour, int max_passes) {
    const std::size_t size = matrix.size();
    for (const auto &row : matrix) {
        if (row.size() != size) {
            throw std::invalid_argument("distance matrix must be square");
        }
    }
    for (int node : tour) {
        if (node < 0 || static_cast<std::size_t>(node) >= size) {
            throw std::invalid_argument("tour contains an index outside the matrix");
        }
    }

    const int n = static_cast<int>(tour.size());
    if (n < 4) {
        return tour;
    }

    std::vector<double> flat(size * size);
    for (std::size_t r = 0; r < size; ++r) {
        for (std::size_t c = 0; c < size; ++c) {
            flat[r * size + c] = matrix[r][c];
        }
    }

    bool improved = true;
    int passes = 0;
    while (improved && passes < max_passes) {
        improved = false;
        ++passes;
        for (int i = 1; i < n - 1; ++i) {
            for (int j = i + 1; j < n; ++j) {
                const std::size_t a = static_cast<std::size_t>(tour[i - 1]);
                const std::size_t b = static_cast<std::size_t>(tour[i]);
                const std::size_t c = static_cast<std::size_t>(tour[j]);
                const std::size_t d = static_cast<std::size_t>(tour[(j + 1 == n) ? 0 : j + 1]);
                const double delta =
                    (flat[a * size + c] + flat[b * size + d]) -
                    (flat[a * size + b] + flat[c * size + d]);
                if (delta < -kEps) {
                    std::reverse(tour.begin() + i, tour.begin() + j + 1);
                    improved = true;
                }
            }
        }
    }
    return tour;
}

PYBIND11_MODULE(_cpp_two_opt, m) {
    m.doc() = "C++ 2-opt local search (optional accelerator for routewise).";
    m.def("two_opt", &two_opt, py::arg("matrix"), py::arg("tour"),
          py::arg("max_passes") = 100, py::call_guard<py::gil_scoped_release>(),
          "2-opt local search; returns a new tour list.");
}