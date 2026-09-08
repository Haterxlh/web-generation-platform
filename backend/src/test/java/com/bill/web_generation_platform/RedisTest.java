package com.bill.web_generation_platform;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.beans.factory.annotation.Value;

@SpringBootTest
class WebGenerationPlatformApplicationTests {

	@Test
	void contextLoads() {
	}

}

@SpringBootTest
public class RedisTest {
	@Autowired
	private StringRedisTemplate redisTemplate;

	@Value("${dashscope.api-key}")
    private String apiKey;

	@Test
	void testRedis() {
		System.out.println(apiKey);
		System.out.println("Hello from test");
		redisTemplate.opsForValue().set("test-key", "Hello Redis!");
		System.out.println(redisTemplate.opsForValue().get("test-key"));
	}
}