"""Armazenamento de imagens no Cloudinary (API do Cloudinary simulada — nenhuma chamada externa)."""

import io
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.core.cloudinary_storage import CloudinaryMediaStorage
from apps.products.models import Product

from .factories import CompanyTestCase

CFG = {"CLOUDINARY_URL": "cloudinary://123456789012345:segredo-de-teste@nuvem-teste",
       "CLOUDINARY_FOLDER": "frenzeltech-ecommerce"}


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 49, 49)).save(buf, format="PNG")
    return buf.getvalue()


@override_settings(**CFG)
class CloudinaryStorageUnitTests(SimpleTestCase):
    def setUp(self):
        self.storage = CloudinaryMediaStorage()

    def test_names_are_unique_and_keep_folder_structure(self):
        a = self.storage.get_available_name("produtos/2026/11/camiseta.png")
        b = self.storage.get_available_name("produtos/2026/11/camiseta.png")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("produtos/2026/11/camiseta-") and a.endswith(".png"))
        self.assertEqual(self.storage.public_id(a), "frenzeltech-ecommerce/" + a[:-4])

    def test_url_is_https_and_optimized(self):
        url = self.storage.url("produtos/2026/11/camiseta-abcd1234.png")
        self.assertTrue(url.startswith("https://res.cloudinary.com/nuvem-teste/image/upload/"))
        self.assertIn("f_auto", url)
        self.assertIn("q_auto", url)
        self.assertIn("frenzeltech-ecommerce/produtos/2026/11/camiseta-abcd1234", url)
        self.assertEqual(self.storage.url(""), "")

    def test_upload_and_delete_call_the_api(self):
        with mock.patch("cloudinary.uploader.upload") as upload, mock.patch("cloudinary.uploader.destroy") as destroy:
            name = self.storage.save("produtos/x.png", SimpleUploadedFile("x.png", png_bytes()))
            upload.assert_called_once()
            self.assertEqual(upload.call_args.kwargs["public_id"], self.storage.public_id(name))
            self.assertFalse(upload.call_args.kwargs["overwrite"])
            self.storage.delete(name)
            destroy.assert_called_once_with(self.storage.public_id(name), resource_type="image", invalidate=True)


@override_settings(**CFG, STORAGES={"default": {"BACKEND": "apps.core.cloudinary_storage.CloudinaryMediaStorage"},
                                    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class CloudinaryProductImageTests(CompanyTestCase):
    def test_product_image_goes_to_cloudinary(self):
        data = {"name": "Camiseta", "sku": "CAM-1", "unit": "un", "acquisition_cost": "10", "purchase_freight": "0",
                "other_costs": "0", "sale_price": "30", "min_stock": "0", "lead_time_days": "7", "safety_stock": "0",
                "is_active": "on", "image": SimpleUploadedFile("camiseta.png", png_bytes(), content_type="image/png")}
        with mock.patch("cloudinary.uploader.upload") as upload:
            response = self.client.post(reverse("products:create"), data)
        self.assertEqual(response.status_code, 302)
        upload.assert_called_once()
        product = Product.objects.get()
        self.assertTrue(product.image.name.startswith("produtos/"))
        self.assertIn("res.cloudinary.com/nuvem-teste", product.image.url)
        page = self.client.get(reverse("products:detail", args=[product.pk]))
        self.assertContains(page, "res.cloudinary.com/nuvem-teste")
