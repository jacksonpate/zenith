/**
 * @file tests/unit/test_clipboard.cpp
 * @brief Wire-format and blob-store tests for clipboard sync (src/clipboard.h).
 */
#include <filesystem>
#include <fstream>

#include <nlohmann/json.hpp>

#include "src/clipboard.h"

#include "../tests_common.h"

namespace {

  clipboard::frame_t make_frame(clipboard::kind_e kind, std::string payload, std::uint32_t token = 0xA1B2C3D4) {
    clipboard::frame_t f;
    f.kind = kind;
    f.token = token;
    f.payload.assign(payload.begin(), payload.end());
    return f;
  }

}  // namespace

TEST(ClipboardWire, RoundTripText) {
  auto wire = clipboard::encode(make_frame(clipboard::kind_e::text, "hello clipboard"));
  ASSERT_FALSE(wire.empty());

  auto decoded = clipboard::decode(wire.data(), wire.size());
  ASSERT_TRUE(decoded.has_value());
  EXPECT_EQ(decoded->kind, clipboard::kind_e::text);
  EXPECT_EQ(decoded->token, 0xA1B2C3D4u);
  EXPECT_EQ(std::string(decoded->payload.begin(), decoded->payload.end()), "hello clipboard");
}

TEST(ClipboardWire, HeaderLayoutMatchesFoundationSpec) {
  // u8 version=1 | u8 kind | u32 token LE | u32 length LE | payload
  auto wire = clipboard::encode(make_frame(clipboard::kind_e::png, "abc", 0x01020304));
  ASSERT_EQ(wire.size(), 10u + 3u);
  EXPECT_EQ(wire[0], 1);  // version
  EXPECT_EQ(wire[1], 2);  // kind png
  EXPECT_EQ(wire[2], 0x04);  // token little-endian
  EXPECT_EQ(wire[3], 0x03);
  EXPECT_EQ(wire[4], 0x02);
  EXPECT_EQ(wire[5], 0x01);
  EXPECT_EQ(wire[6], 3);  // length little-endian
  EXPECT_EQ(wire[7], 0);
  EXPECT_EQ(wire[8], 0);
  EXPECT_EQ(wire[9], 0);
  EXPECT_EQ(wire[10], 'a');
}

TEST(ClipboardWire, RejectsMalformedInput) {
  auto wire = clipboard::encode(make_frame(clipboard::kind_e::text, "payload"));

  EXPECT_FALSE(clipboard::decode(nullptr, 0).has_value());
  EXPECT_FALSE(clipboard::decode(wire.data(), 9).has_value());  // truncated header

  auto bad_version = wire;
  bad_version[0] = 2;
  EXPECT_FALSE(clipboard::decode(bad_version.data(), bad_version.size()).has_value());

  auto bad_kind = wire;
  bad_kind[1] = 9;
  EXPECT_FALSE(clipboard::decode(bad_kind.data(), bad_kind.size()).has_value());

  auto bad_length = wire;
  bad_length[6] += 1;  // length no longer matches the buffer
  EXPECT_FALSE(clipboard::decode(bad_length.data(), bad_length.size()).has_value());
}

TEST(ClipboardWire, AllKindsRoundTrip) {
  for (auto kind : {clipboard::kind_e::text, clipboard::kind_e::png, clipboard::kind_e::ref, clipboard::kind_e::file_offer}) {
    auto wire = clipboard::encode(make_frame(kind, "x"));
    auto decoded = clipboard::decode(wire.data(), wire.size());
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(decoded->kind, kind);
  }
}

TEST(ClipboardBlob, PutTakeConsumes) {
  auto id = clipboard::blob::put("image/png", {1, 2, 3, 4});
  ASSERT_FALSE(id.empty());

  auto blob = clipboard::blob::take(id);
  ASSERT_TRUE(blob.has_value());
  EXPECT_EQ(blob->first, "image/png");
  EXPECT_EQ(blob->second, (std::vector<std::uint8_t> {1, 2, 3, 4}));

  EXPECT_FALSE(clipboard::blob::take(id).has_value());  // consumed
}

TEST(ClipboardBlob, UnknownIdsMiss) {
  EXPECT_FALSE(clipboard::blob::take("nope").has_value());
  EXPECT_FALSE(clipboard::blob::file_path("nope").has_value());
}

TEST(ClipboardOffer, RegistersFileAndBuildsFoundationSchema) {
  auto path = std::filesystem::temp_directory_path() / "zenith-clipboard-offer-test.bin";
  {
    std::ofstream out(path, std::ios::binary);
    out << "0123456789";
  }

  auto offer_text = clipboard::offer_file(path.string());
  ASSERT_FALSE(offer_text.empty());

  auto offer = nlohmann::json::parse(offer_text);
  EXPECT_EQ(offer.at("name").get<std::string>(), "zenith-clipboard-offer-test.bin");
  EXPECT_EQ(offer.at("size").get<std::uint64_t>(), 10u);
  EXPECT_EQ(offer.at("type").get<std::string>(), "file");
  EXPECT_TRUE(offer.contains("id"));
  EXPECT_TRUE(offer.contains("download_url"));
  EXPECT_TRUE(offer.contains("expires_in"));

  auto resolved = clipboard::blob::file_path(offer.at("id").get<std::string>());
  ASSERT_TRUE(resolved.has_value());
  EXPECT_EQ(std::filesystem::canonical(*resolved), std::filesystem::canonical(path));

  std::filesystem::remove(path);
}

TEST(ClipboardOffer, RejectsMissingAndNonRegularPaths) {
  EXPECT_TRUE(clipboard::offer_file("/definitely/not/a/file").empty());
  EXPECT_TRUE(clipboard::offer_file(std::filesystem::temp_directory_path().string()).empty());
}
