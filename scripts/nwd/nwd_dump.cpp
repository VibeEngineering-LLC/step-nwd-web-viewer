#include <nwd/reader.hpp>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#include <cstdint>
#include <cmath>
#include <algorithm>
#include <unordered_map>
#include <stack>
#include <stdexcept>
#include <map>
#include "sysprops.hpp"
#include <iomanip>
#include <cstdio>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#endif

namespace fs = std::filesystem;

static void escape_json(std::ostream& os, std::string_view s) {
    os << '"';
    for (unsigned char c : s) {
        if (c == '"') os << "\\\"";
        else if (c == '\\') os << "\\\\";
        else if (c < 0x20) {
            char buf[8];
            std::snprintf(buf, sizeof(buf), "\\u%04x", c);
            os << buf;
        } else {
            os << static_cast<char>(c);
        }
    }
    os << '"';
}

static void write_float(std::ostream& os, float v) {
    if (!std::isfinite(v)) {
        os << "null";
    } else {
        os << std::setprecision(9) << static_cast<double>(v);
    }
}

static void write_double(std::ostream& os, double v) {
    if (!std::isfinite(v)) {
        os << "null";
    } else {
        os << std::setprecision(17) << v;
    }
}

static void write_float_array(std::ostream& os, const std::vector<float>& arr) {
    os << '[';
    for (size_t i = 0; i < arr.size(); ++i) {
        if (i) os << ',';
        write_float(os, arr[i]);
    }
    os << ']';
}

static void write_double_array(std::ostream& os, const std::vector<double>& arr) {
    os << '[';
    for (size_t i = 0; i < arr.size(); ++i) {
        if (i) os << ',';
        write_double(os, arr[i]);
    }
    os << ']';
}

static void write_double_array16(std::ostream& os, const std::array<double, 16>& arr) {
    os << '[';
    for (size_t i = 0; i < arr.size(); ++i) {
        if (i) os << ',';
        write_double(os, arr[i]);
    }
    os << ']';
}

int run(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "Usage: nwd_dump INPUT.nwd OUTDIR\n";
        return 2;
    }

    fs::path input = argv[1];
    fs::path outdir = argv[2];

    try {
        fs::create_directories(outdir);

        nwd::Options opts;
        opts.threads = 0;
        opts.products = true;
        nwd::Document doc(input, opts);
        auto scene = doc.read_scene();

        fs::path json_path = outdir / "scene.json";
        fs::path bin_path = outdir / "geoms.bin";

        std::ofstream json_out(json_path, std::ios::binary);
        std::ofstream bin_out(bin_path, std::ios::binary);

        if (!json_out.is_open()) throw std::runtime_error("Failed to open scene.json for writing");
        if (!bin_out.is_open()) throw std::runtime_error("Failed to open geoms.bin for writing");

        uint64_t bin_offset = 0;

        size_t total_models = scene.models.size();
        size_t total_nodes = 0;
        size_t total_instances = 0;
        size_t total_geometries = 0;
        size_t matrix_failures = 0;
        size_t strip_failures = 0;

        size_t count_94 = 0, count_95 = 0, count_96 = 0, count_103 = 0, count_104 = 0, count_105 = 0, count_181 = 0;

        json_out << "{\"models\":[";

        for (size_t mi = 0; mi < scene.models.size(); ++mi) {
            const auto& m = scene.models[mi];

            if (mi > 0) json_out << ',';
            json_out << "{\"name\":";
            escape_json(json_out, m.name);
            json_out << ",\"meters_per_unit\":";
            write_double(json_out, m.meters_per_unit);
            json_out << ",\"linear_units\":" << m.linear_units;

            // Nodes
            json_out << ",\"nodes\":[";
            nwd::ModelIndex idx(m);
            std::vector<char> visited(m.paths.size(), 0);
            std::stack<std::pair<nwd::Id, int64_t>> stk; // (path, parent)
            stk.push({0, -1});
            bool first_node = true;
            while (!stk.empty()) {
                auto [p, parent] = stk.top();
                stk.pop();
                if (p >= m.paths.size() || visited[p]) continue;
                visited[p] = 1;
                std::string_view name;
                try {
                    auto ref = nwd::path_reference(m, p);
                    name = nwd::resolve_string(m.graphs.at(ref.graph), nwd::path_object(m, p).name);
                } catch (...) {}
                if (!first_node) json_out << ',';
                first_node = false;
                json_out << "\n{\"id\":" << p << ",\"parent\":" << parent << ",\"name\":";
                escape_json(json_out, name);
                json_out << '}';
                total_nodes++;
                auto ch = idx.children(p);
                for (size_t k = ch.size(); k-- > 0;) stk.push({ch[k], static_cast<int64_t>(p)});
            }
            json_out << "],\"instances\":[";

            // Instances
            size_t total_instances_m = 0;
            std::map<std::string, size_t> sys_ids;
            std::vector<std::string> sys_names;
            std::unordered_map<nwd::Id, int64_t> sys_of_path;
            for (size_t ii = 0; ii < m.instances.size(); ++ii) {
                const auto& inst = m.instances[ii];

                nwd::Id geom = nwd::none;
                if (inst.geometry_reference != nwd::none && inst.geometry_reference < m.geometry_references.size()) {
                    geom = m.geometry_references[inst.geometry_reference].geometry;
                }

                float r = 0.7f, g = 0.7f, b = 0.7f;
                float tr = 0.0f;
                if (inst.appearance != nwd::none && inst.appearance < m.appearances.size()) {
                    const auto& app = m.appearances[inst.appearance];
                    if (app.material != nwd::none && app.material < m.materials.size()) {
                        const auto& mat = m.materials[app.material];
                        auto diff = mat.diffuse();
                        r = diff[0]; g = diff[1]; b = diff[2];
                        tr = mat.transparency();
                    }
                }

                std::array<double, 16> mat16;
                try {
                    mat16 = nwd::world_matrix(m, inst);
                } catch (...) {
                    matrix_failures++;
                    continue;
                }

                if (total_instances_m++) json_out << ',';
                int64_t sid = -1;
                auto cached = sys_of_path.find(inst.path);
                if (cached != sys_of_path.end()) sid = cached->second;
                else {
                    std::string sv = find_property(m, inst.path, "IfcSystem");
                    if (!sv.empty()) {
                        auto ins = sys_ids.try_emplace(sv, sys_names.size());
                        if (ins.second) sys_names.push_back(sv);
                        sid = static_cast<int64_t>(ins.first->second);
                    }
                    sys_of_path[inst.path] = sid;
                }
                json_out << "\n{\"path\":" << inst.path << ",\"s\":" << sid << ",\"f\":" << inst.flags;
                json_out << ",\"geom\":" << (geom == nwd::none ? -1 : static_cast<int>(geom));
                json_out << ",\"m\":";
                write_double_array16(json_out, mat16);
                json_out << ",\"c\":[";
                write_float(json_out, r); json_out << ',';
                write_float(json_out, g); json_out << ',';
                write_float(json_out, b);
                json_out << "],\"t\":";
                write_float(json_out, tr);
                json_out << '}';
                total_instances++;
            }
            json_out << "],\"systems\":[";
            for (size_t k = 0; k < sys_names.size(); ++k) { if (k) json_out << ','; escape_json(json_out, sys_names[k]); }
            json_out << "],\"geoms\":[";

            // Geometries
            for (size_t gi = 0; gi < m.geometries.size(); ++gi) {
                const auto& g = m.geometries[gi];
                if (gi > 0) json_out << ',';

                int t = static_cast<int>(g.type);
                if (t == 94) count_94++;
                else if (t == 95) count_95++;
                else if (t == 96) count_96++;
                else if (t == 103) count_103++;
                else if (t == 104) count_104++;
                else if (t == 105) count_105++;
                else if (t == 181) count_181++;

                int64_t vo = -1, vn = 0, io = -1, in = 0;

                if (t == 94 && g.external == nullptr) {
                    try {
                        auto tris = nwd::triangle_indices(g);
                        // Compact slots
                        std::unordered_map<nwd::Id, uint32_t> slot_map;
                        std::vector<float> verts;
                        std::vector<uint32_t> idxs;

                        for (size_t ti = 0; ti < tris.size(); ti += 3) {
                            for (int k = 0; k < 3; ++k) {
                                nwd::Id slot = tris[ti + k];
                                if (slot >= g.coordinate_indices.size()) continue;
                                nwd::Id coord_idx = g.coordinate_indices[slot];
                                if (coord_idx * 3 + 2 >= g.coordinates.size()) continue;

                                auto it = slot_map.find(slot);
                                if (it == slot_map.end()) {
                                    uint32_t new_id = static_cast<uint32_t>(verts.size() / 3);
                                    slot_map[slot] = new_id;
                                    verts.push_back(g.coordinates[coord_idx * 3 + 0]);
                                    verts.push_back(g.coordinates[coord_idx * 3 + 1]);
                                    verts.push_back(g.coordinates[coord_idx * 3 + 2]);
                                }
                                idxs.push_back(slot_map[slot]);
                            }
                        }

                        vo = static_cast<int64_t>(bin_offset);
                        vn = static_cast<int64_t>(verts.size() / 3);
                        bin_out.write(reinterpret_cast<const char*>(verts.data()), verts.size() * sizeof(float));
                        bin_offset += verts.size() * sizeof(float);

                        io = static_cast<int64_t>(bin_offset);
                        in = static_cast<int64_t>(idxs.size());
                        bin_out.write(reinterpret_cast<const char*>(idxs.data()), idxs.size() * sizeof(uint32_t));
                        bin_offset += idxs.size() * sizeof(uint32_t);
                    } catch (...) {
                        strip_failures++;
                    }
                }

                json_out << "\n{\"type\":" << t;
                json_out << ",\"flags\":" << g.flags;
                json_out << ",\"params\":[";
                for (int i = 0; i < 13; ++i) {
                    if (i) json_out << ',';
                    write_float(json_out, g.parameters[i]);
                }
                json_out << "],\"vo\":" << vo;
                json_out << ",\"vn\":" << vn;
                json_out << ",\"io\":" << io;
                json_out << ",\"in\":" << in;
                json_out << '}';
                total_geometries++;
            }
            json_out << "]}";
        }

        json_out << "],\"overrides\":[";
        bool first_ov = true;
        if (scene.products) {
            for (const auto& blk : scene.products->blocks) {
                const auto* ov = std::get_if<nwd::NodeOverrides>(&blk.value);
                if (!ov) continue;
                for (const auto& p : ov->paths) {
                    if (!first_ov) json_out << ',';
                    first_ov = false;
                    json_out << "\n{\"path\":" << p.path_link << ",\"flags\":" << p.flags << ",\"mv\":" << ov->mask_value_encoding << ",\"ok\":" << ov->associations_verified << '}';
                }
                for (const auto& n : ov->nodes) {
                    if (!first_ov) json_out << ',';
                    first_ov = false;
                    json_out << "\n{\"nodes\":[";
                    for (size_t k = 0; k < n.paths.size(); ++k) json_out << (k ? "," : "") << n.paths[k];
                    json_out << "],\"flags\":" << n.flags << ",\"mv\":" << ov->mask_value_encoding << '}';
                }
            }
        }
        json_out << "],\"stats\":{\"models\":" << total_models;
        json_out << ",\"nodes\":" << total_nodes;
        json_out << ",\"instances\":" << total_instances;
        json_out << ",\"geometries\":" << total_geometries;
        json_out << ",\"matrix_failures\":" << matrix_failures;
        json_out << ",\"strip_failures\":" << strip_failures;
        json_out << ",\"type_94\":" << count_94;
        json_out << ",\"type_95\":" << count_95;
        json_out << ",\"type_96\":" << count_96;
        json_out << ",\"type_103\":" << count_103;
        json_out << ",\"type_104\":" << count_104;
        json_out << ",\"type_105\":" << count_105;
        json_out << ",\"type_181\":" << count_181;
        json_out << "}}";

        json_out.close();
        bin_out.close();

        std::cout << "Models: " << total_models
                  << ", Nodes: " << total_nodes
                  << ", Instances: " << total_instances
                  << ", Geometries: " << total_geometries
                  << " (94:" << count_94
                  << " 95:" << count_95
                  << " 96:" << count_96
                  << " 103:" << count_103
                  << " 104:" << count_104
                  << " 105:" << count_105
                  << " 181:" << count_181 << ")\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << e.what() << "\n";
        return 1;
    }
}

#ifdef _WIN32
int wmain(int argc, wchar_t** argv) {
    std::vector<std::string> args;
    for (int i = 0; i < argc; ++i) {
        int len = WideCharToMultiByte(CP_UTF8, 0, argv[i], -1, nullptr, 0, nullptr, nullptr);
        std::string s(len - 1, '\0');
        WideCharToMultiByte(CP_UTF8, 0, argv[i], -1, &s[0], len, nullptr, nullptr);
        args.push_back(s);
    }
    std::vector<char*> cargs;
    for (auto& s : args) cargs.push_back(&s[0]);
    return run(argc, cargs.data());
}
#else
int main(int argc, char** argv) {
    return run(argc, argv);
}
#endif
