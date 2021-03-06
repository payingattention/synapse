# Copyright 2014, 2015 OpenMarket Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from ._base import Config


class CaptchaConfig(Config):

    def read_config(self, config):
        self.recaptcha_private_key = config["recaptcha_private_key"]
        self.recaptcha_public_key = config["recaptcha_public_key"]
        self.enable_registration_captcha = config["enable_registration_captcha"]
        # XXX: This is used for more than just captcha
        self.captcha_ip_origin_is_x_forwarded = (
            config["captcha_ip_origin_is_x_forwarded"]
        )
        self.captcha_bypass_secret = config.get("captcha_bypass_secret")

    def default_config(self, config_dir_path, server_name):
        return """\
        ## Captcha ##

        # This Home Server's ReCAPTCHA public key.
        recaptcha_private_key: "YOUR_PUBLIC_KEY"

        # This Home Server's ReCAPTCHA private key.
        recaptcha_public_key: "YOUR_PRIVATE_KEY"

        # Enables ReCaptcha checks when registering, preventing signup
        # unless a captcha is answered. Requires a valid ReCaptcha
        # public/private key.
        enable_registration_captcha: False

        # When checking captchas, use the X-Forwarded-For (XFF) header
        # as the client IP and not the actual client IP.
        captcha_ip_origin_is_x_forwarded: False

        # A secret key used to bypass the captcha test entirely.
        #captcha_bypass_secret: "YOUR_SECRET_HERE"
        """
